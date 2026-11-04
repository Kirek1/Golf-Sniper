# Golf 7 Sniper 🚗

Bot, który co kilka minut przegląda ogłoszenia, ocenia każdego Golfa 7 / 7.5
na tle rynku i wysyła na Telegram tylko te warte telefonu.

## Czego szuka
- Golf 7 (2013–2016) i Golf 7.5 (2017–2020), **tylko 5-drzwiowy hatchback**
- do 40 000 zł (do 44 000 zł przy „do negocjacji”), przebieg do 180 tys. km (7.5: 200 tys.)
- wersje: **R-Line** (priorytet), **GTI, GTD**, Highline, Join, Sound, Lounge, IQ.Drive,
  Comfortline, Allstar, Trendline, BlueMotion; benzyna i diesel, manual i DSG
- pomija: Variant, Sportsvan, Alltrack, 3-drzwiowe, e-Golf, GTE; Golf R za te pieniądze = czerwona flaga

## Skąd bierze oferty
| Portal | Jak | Uwagi |
|---|---|---|
| OtoMoto | strona wyników + ogłoszenie (VIN, ASO, drzwi) | największe źródło |
| OLX | dane ze strony wyników | ogłoszenia przekierowujące na OtoMoto są pomijane |
| Autoplac | dane ze strony wyników | blokuje serwery (403) – domyślnie wyłączony, włączysz `AUTOPLAC=1` w `.env` |
| Sprzedajemy | lista + strona ogłoszenia | |
| AutoScout24 | auta z DE/NL/BE/AT/IT… | cena = auto w € po kursie NBP + akcyza 3,1% + ~3 500 zł na sprowadzenie. Wyłączysz przez `IMPORT=0` w `.env` |

Facebook Marketplace (wymaga logowania) i Allegro Lokalnie (blokuje boty) nie są obsługiwane.
To samo auto na kilku portalach (ten sam VIN albo rocznik + przebieg + cena) zgłaszane jest raz.

## Uruchomienie na VPS (Ubuntu)
```bash
sudo apt update && sudo apt install -y git python3-venv
git clone https://github.com/Kirek1/Golf-Sniper.git ~/golf-sniper
cd ~/golf-sniper
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
nano .env          # TELEGRAM_TOKEN=…  i  TELEGRAM_CHAT_ID=…
.venv/bin/python -m sniper.main --once --dry-run      # test: alerty w konsoli
```
Praca ciągła jako usługa (sama wstaje po restarcie serwera):
```bash
sudo tee /etc/systemd/system/golf-sniper.service > /dev/null <<EOF
[Unit]
Description=Golf 7 Sniper
After=network-online.target

[Service]
User=$USER
Environment=TZ=Europe/Warsaw
WorkingDirectory=$HOME/golf-sniper
ExecStart=$HOME/golf-sniper/.venv/bin/python -m sniper.main
Restart=always
RestartSec=30
Nice=10
MemoryHigh=500M
MemoryMax=900M

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now golf-sniper
```
Aktualizacja: `cd ~/golf-sniper && git pull && .venv/bin/pip install -r requirements.txt && sudo systemctl restart golf-sniper`
Logi: `sudo journalctl -u golf-sniper -n 50 --no-pager`

## Telegram
Na starcie bot wysyła wiadomość z opisem, czego szuka. Komendy:
`/start` (to samo jeszcze raz), `/status`, `/raport`, `/ulubione`, `/pauza`, `/wznow`.

| Alert | Wynik | Kiedy |
|---|---|---|
| 💎 Perełka | 80+ | od razu (tylko gdy wycena jest pewna) |
| 🔥 Okazja | 65+ | od razu |
| 📋 Raport | 50–64 | podsumowanie o 20:00 |

Pod alertem: ⭐ (ulubione) i 🙈 (nie pokazuj więcej).

## Jak liczony jest wynik (0–100)
Cena vs rynek 40 · historia (VIN, salon PL, ASO, bezwypadkowy) 20 · przebieg względem wieku 15 ·
wersja (R-Line, GTI, 7.5, DSG) 10 · sprzedający prywatny 10 · świeżość/obniżka 5.
Czerwone flagi: cena >25% poniżej rynku, brak VIN, podejrzanie niski przebieg, zaliczka/„auto za granicą”,
VIN wystawiany wcześniej z większym przebiegiem, Golf R za 40 tys.

Wycena to mediany cen podobnych aut (rocznik × przebieg × skrzynia × paliwo × R-Line) z ostatnich
90 dni. Pierwsze 2–3 tygodnie jest zgrubna – wtedy bot nie ogłasza „perełek”, tylko „okazje”.

## GitHub Actions (zapas)
Harmonogram na GitHubie jest wyłączony, bo bot chodzi na VPS-ie. Ręcznie: Actions → Golf 7 Sniper →
Run workflow (wymaga sekretów `TELEGRAM_TOKEN` i `TELEGRAM_CHAT_ID`).

## Testy
`python -m pytest` – działają offline, m.in. na zapisanych prawdziwych stronach OLX i Autoplac.

## Przed telefonem do sprzedającego
- VIN na historiapojazdu.gov.pl (VIN + numer rejestracyjny + data 1. rejestracji); przy imporcie płatny raport.
- Miernik lakieru i diagnostyka OBD – porównaj przebieg ze sterowników.
- DSG (DQ200): zapytaj o olej i mechatronikę. Prawdziwy R-Line: kierownica ze spłaszczonym dołem,
  sportowe fotele, progi, logo na błotnikach.
- Nie wpłacaj zaliczki za auto, którego nie widziałeś.
