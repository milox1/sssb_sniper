# SSSB Discord sniper

Bot sprawdza ogloszenia SSSB i wysyla na Discorda tylko nowe pozycje, ktore maja
date wprowadzenia od `2026-08-01`.

## Lokalnie

1. Skopiuj `.env.example` do `.env`.
2. Wklej swoj Discord webhook jako `DISCORD_WEBHOOK_URL`.
3. Zrob test bez wysylania:

```powershell
python sssb_sniper.py --once --dry-run --debug
```

4. Jesli lista wyglada dobrze, odpal normalnie:

```powershell
python sssb_sniper.py
```

Domyslnie pierwszy run zapisuje znalezione pasujace ogloszenia jako widziane i
nie wysyla ich na Discorda. Jesli chcesz dostac takze pierwsza paczke obecnych
ogloszen, ustaw:

```text
SEND_ON_FIRST_RUN=true
```

## Discord webhook

W Discordzie wejdz w:

`Server Settings -> Integrations -> Webhooks -> New Webhook -> Copy Webhook URL`

Webhook trzymaj w `.env` lokalnie albo w GitHub Secrets, nigdy w publicznym
pliku repo.

## GitHub Actions, czyli darmowe prawie-24/7

Workflow w `.github/workflows/sssb-sniper.yml` odpala bota:

- co 15 minut w dni robocze,
- mocniej w typowych oknach SSSB: poniedzialek 16:00 i czwartek 10:00,
- latem dodatkowo co 5 minut w srody i piatki w godzinach roboczych.

SSSB pisze, ze zwykle publikuja wolne mieszkania w poniedzialki 16:00 i
czwartki 10:00, ale moga publikowac we wszystkie dni robocze. Latem moga
publikowac w poniedzialki, srody i piatki.

GitHub uzywa czasu UTC. Aktualny workflow jest ustawiony pod czas letni w Polsce
czyli `16:00 Warszawa = 14:00 UTC`. GitHub nie gwarantuje startu idealnie co do
sekundy, dlatego workflow odpala kilka sprawdzen wokol najwazniejszych godzin.

W repo dodaj secret:

`Settings -> Secrets and variables -> Actions -> New repository secret`

Nazwa:

```text
DISCORD_WEBHOOK_URL
```

Wartosc: caly Discord webhook URL.

Workflow zapisuje `.sssb_seen.json` do repo, zeby nie wysylac duplikatow przy
kolejnych uruchomieniach.

## Najwazniejsze ustawienia

`MIN_MOVE_IN=2026-08-01` filtruje date.

`DATE_MODE=on_or_after` oznacza: data wprowadzenia ma byc 1 sierpnia 2026 albo
pozniej.

`MAX_PAGES=1` sprawdza pierwsza strone wynikow. Jesli SSSB zacznie pokazywac
wiecej stron i endpoint przyjmie parametr strony, zwieksz te wartosc.
