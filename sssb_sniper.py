from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


DEFAULT_SEARCH_URL = (
    "https://minasidor.sssb.se/en/available-apartments/"
    "?actionId=&omraden=&oboTyper=&hyraMax="
)
WIDGET_ENDPOINT = "https://minasidor.sssb.se/widgets/"
OBJECT_WIDGET = "objektlistabilder@lagenheter"
PAGINATION_WIDGET = "paginering@lagenheter"


MONTHS = {
    "jan": 1,
    "january": 1,
    "januari": 1,
    "feb": 2,
    "february": 2,
    "februari": 2,
    "mar": 3,
    "march": 3,
    "mars": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "maj": 5,
    "jun": 6,
    "june": 6,
    "juni": 6,
    "jul": 7,
    "july": 7,
    "juli": 7,
    "aug": 8,
    "august": 8,
    "augusti": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "okt": 10,
    "oktober": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


@dataclass(frozen=True)
class Config:
    search_url: str
    min_move_in: date
    date_mode: str
    discord_webhook_url: str | None
    state_path: Path
    poll_seconds: int
    send_on_first_run: bool
    max_pages: int
    page_param: str
    timeout_seconds: int


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def getenv_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_iso_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise SystemExit(f"{field_name} musi byc w formacie YYYY-MM-DD, np. 2026-08-01") from exc


def load_config() -> Config:
    load_dotenv()
    date_mode = os.getenv("DATE_MODE", "on_or_after").strip().lower()
    if date_mode not in {"on_or_after", "on_or_before"}:
        raise SystemExit("DATE_MODE ustaw na on_or_after albo on_or_before")

    return Config(
        search_url=os.getenv("SSSB_SEARCH_URL", DEFAULT_SEARCH_URL),
        min_move_in=parse_iso_date(os.getenv("MIN_MOVE_IN", "2026-08-01"), "MIN_MOVE_IN"),
        date_mode=date_mode,
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
        state_path=Path(os.getenv("STATE_PATH", ".sssb_seen.json")),
        poll_seconds=int(os.getenv("POLL_SECONDS", "180")),
        send_on_first_run=getenv_bool("SEND_ON_FIRST_RUN", False),
        max_pages=int(os.getenv("MAX_PAGES", "1")),
        page_param=os.getenv("PAGE_PARAM", "page"),
        timeout_seconds=int(os.getenv("TIMEOUT_SECONDS", "25")),
    )


def build_widgets_url(config: Config, page: int = 1) -> str:
    parsed = urllib.parse.urlparse(config.search_url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [
        (key, value)
        for key, value in query
        if key not in {"callback", "_", "widgets[]", "widgets"}
    ]
    query.append(("widgets[]", OBJECT_WIDGET))
    query.append(("widgets[]", PAGINATION_WIDGET))
    query.append(("callback", "sssbBot"))
    if page > 1:
        query.append((config.page_param, str(page)))
    return WIDGET_ENDPOINT + "?" + urllib.parse.urlencode(query)


def fetch_text(url: str, timeout_seconds: int) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json,text/javascript,*/*;q=0.8",
            "Referer": DEFAULT_SEARCH_URL,
            "User-Agent": "Mozilla/5.0 (compatible; SSSBNotifier/1.0)",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_json_or_jsonp(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)

    match = re.match(r"^[\w$.]+\((.*)\);?\s*$", stripped, flags=re.S)
    if not match:
        raise ValueError("Nie rozpoznalem odpowiedzi SSSB jako JSON ani JSONP")
    return json.loads(match.group(1))


def get_nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def find_apartment_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    direct = get_nested(payload, "data", OBJECT_WIDGET)
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, dict)]

    nested = get_nested(payload, "data", "data", OBJECT_WIDGET)
    if isinstance(nested, list):
        return [item for item in nested if isinstance(item, dict)]

    def walk(value: Any) -> list[dict[str, Any]] | None:
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            keys = set().union(*(item.keys() for item in value))
            if {"inflyttningDatum", "detaljUrl"} & keys:
                return value
        if isinstance(value, dict):
            for child in value.values():
                found = walk(child)
                if found is not None:
                    return found
        return None

    return walk(payload) or []


def parse_listing_date(value: Any) -> date | None:
    if value is None:
        return None
    text = html.unescape(str(value)).strip()
    if not text or text in {"-", "None", "null"}:
        return None

    normalized = re.sub(r"\s+", " ", text.lower())
    normalized = normalized.replace(",", " ")

    patterns = [
        (r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", ("y", "m", "d")),
        (r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", ("d", "m", "y")),
    ]
    for pattern, order in patterns:
        match = re.search(pattern, normalized)
        if match:
            values = dict(zip(order, map(int, match.groups())))
            try:
                return date(values["y"], values["m"], values["d"])
            except ValueError:
                return None

    match = re.search(r"\b(\d{1,2})\s+([a-zåäö]+)\s+(\d{4})\b", normalized)
    if match:
        day_s, month_s, year_s = match.groups()
        month = MONTHS.get(month_s[:3], MONTHS.get(month_s))
        if month:
            try:
                return date(int(year_s), month, int(day_s))
            except ValueError:
                return None
    return None


def listing_key(listing: dict[str, Any]) -> str:
    for key in ("refid", "rsn", "objektId", "objektNr", "id", "detaljUrl"):
        value = listing.get(key)
        if value:
            return str(value)
    raw = json.dumps(listing, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def absolute_url(value: Any) -> str:
    if not value:
        return DEFAULT_SEARCH_URL
    return urllib.parse.urljoin("https://minasidor.sssb.se", str(value))


def matches_date(move_in: date | None, config: Config) -> bool:
    if move_in is None:
        return False
    if config.date_mode == "on_or_before":
        return move_in <= config.min_move_in
    return move_in >= config.min_move_in


def normalize_listing(listing: dict[str, Any], config: Config) -> dict[str, Any] | None:
    move_in_raw = listing.get("inflyttningDatum") or listing.get("movingIn") or listing.get("MovingIn")
    move_in = parse_listing_date(move_in_raw)
    if not matches_date(move_in, config):
        return None

    title = listing.get("typ") or listing.get("title") or "SSSB listing"
    address = listing.get("adress") or listing.get("address") or ""
    area = listing.get("omrade") or listing.get("area") or ""
    rent = " ".join(
        str(part)
        for part in (listing.get("hyra"), listing.get("hyraEnhet"))
        if part not in {None, ""}
    )
    space = listing.get("yta")
    queue = listing.get("antalIntresse")
    floor = listing.get("vaning")
    url = absolute_url(listing.get("detaljUrl") or listing.get("url"))

    return {
        "key": listing_key(listing),
        "title": str(title),
        "address": str(address),
        "area": str(area),
        "rent": rent,
        "space": f"{space} m2" if space not in {None, ""} else "",
        "move_in": move_in.isoformat() if move_in else str(move_in_raw),
        "queue": str(queue) if queue not in {None, ""} else "",
        "floor": str(floor) if floor not in {None, ""} else "",
        "url": url,
    }


def load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    if isinstance(data, list):
        return {str(item) for item in data}
    if isinstance(data, dict):
        return {str(item) for item in data.get("seen", [])}
    return set()


def save_seen(path: Path, seen: set[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "updated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                "seen": sorted(seen),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def discord_payload(listing: dict[str, Any]) -> dict[str, Any]:
    fields = [
        ("Area", listing["area"]),
        ("Address", listing["address"]),
        ("Move-in", listing["move_in"]),
        ("Rent", listing["rent"]),
        ("Size", listing["space"]),
        ("Queue/interest", listing["queue"]),
        ("Floor", listing["floor"]),
    ]
    return {
        "content": "@everyone Nowe ogloszenie SSSB pasuje do filtra.",
        "allowed_mentions": {"parse": ["everyone"]},
        "embeds": [
            {
                "title": listing["title"],
                "url": listing["url"],
                "color": 0x0B7A75,
                "fields": [
                    {"name": name, "value": value, "inline": True}
                    for name, value in fields
                    if value
                ],
            }
        ],
    }


def post_discord(webhook_url: str, listing: dict[str, Any], timeout_seconds: int) -> None:
    body = json.dumps(discord_payload(listing)).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "SSSBNotifier/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        if response.status >= 300:
            raise RuntimeError(f"Discord webhook zwrocil HTTP {response.status}")


def send_test_webhook(config: Config) -> None:
    if not config.discord_webhook_url:
        raise SystemExit("Brakuje DISCORD_WEBHOOK_URL w .env albo GitHub Secrets")

    test_listing = {
        "title": "SSSB sniper test",
        "url": config.search_url,
        "area": "Webhook check",
        "address": "GitHub Actions",
        "move_in": config.min_move_in.isoformat(),
        "rent": "test message",
        "space": "",
        "queue": "",
        "floor": "",
    }
    post_discord(config.discord_webhook_url, test_listing, config.timeout_seconds)
    print("Discord webhook dziala: wyslano wiadomosc testowa.")


def fetch_listings(config: Config) -> list[dict[str, Any]]:
    listings: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for page in range(1, config.max_pages + 1):
        url = build_widgets_url(config, page=page)
        payload = parse_json_or_jsonp(fetch_text(url, config.timeout_seconds))
        page_listings = find_apartment_list(payload)
        if not page_listings:
            break
        for listing in page_listings:
            key = listing_key(listing)
            if key not in seen_keys:
                seen_keys.add(key)
                listings.append(listing)
    return listings


def run_once(config: Config, dry_run: bool = False, debug: bool = False) -> int:
    state_existed = config.state_path.exists()
    seen = load_seen(config.state_path)
    original_seen = set(seen)
    listings = fetch_listings(config)
    matches = [item for item in (normalize_listing(item, config) for item in listings) if item]

    if debug:
        print(f"Pobrano ogloszen: {len(listings)}")
        print(f"Pasuje do daty: {len(matches)}")

    new_matches = [item for item in matches if item["key"] not in seen]
    should_send = state_existed or config.send_on_first_run

    if dry_run:
        print(f"Znalezione pasujace ogloszenia: {len(matches)}")
        for item in matches[:20]:
            print(f"- {item['move_in']} | {item['title']} | {item['area']} | {item['rent']} | {item['url']}")
        if len(matches) > 20:
            print(f"...i jeszcze {len(matches) - 20}")
    elif new_matches and not should_send:
        print(
            f"Pierwszy run: zapisuje {len(new_matches)} pasujacych ogloszen jako widziane, bez wysylki. "
            "Ustaw SEND_ON_FIRST_RUN=true, jesli chcesz wyslac takze pierwsza paczke."
        )
    elif new_matches:
        if not config.discord_webhook_url:
            raise SystemExit("Brakuje DISCORD_WEBHOOK_URL w .env albo GitHub Secrets")
        for item in new_matches:
            post_discord(config.discord_webhook_url, item, config.timeout_seconds)
            print(f"Wyslano na Discord: {item['title']} ({item['move_in']})")
            time.sleep(1)
    else:
        print("Brak nowych pasujacych ogloszen.")

    seen.update(item["key"] for item in matches)
    if not dry_run and seen != original_seen:
        save_seen(config.state_path, seen)
    return len(new_matches)


def main() -> int:
    parser = argparse.ArgumentParser(description="SSSB apartment notifier for Discord.")
    parser.add_argument("--once", action="store_true", help="Run one check and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Do not send Discord messages.")
    parser.add_argument("--debug", action="store_true", help="Print extra diagnostics.")
    parser.add_argument("--test-webhook", action="store_true", help="Send a Discord test message and exit.")
    args = parser.parse_args()

    config = load_config()

    if args.test_webhook:
        try:
            send_test_webhook(config)
            return 0
        except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as exc:
            print(f"Blad testu webhooka: {exc}", file=sys.stderr)
            return 1

    if args.once:
        try:
            run_once(config, dry_run=args.dry_run, debug=args.debug)
            return 0
        except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as exc:
            print(f"Blad sprawdzania: {exc}", file=sys.stderr)
            return 1

    while True:
        try:
            run_once(config, dry_run=args.dry_run, debug=args.debug)
        except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as exc:
            print(f"Blad sprawdzania: {exc}", file=sys.stderr)
        time.sleep(config.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
