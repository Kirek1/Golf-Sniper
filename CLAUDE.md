# Golf 7 Sniper – kontekst projektu

Bot, który szuka dla właściciela repo używanego **VW Golfa 7 / 7.5** i wysyła okazje na Telegram.
Rozmawiaj z użytkownikiem **po polsku**, prostym językiem – nie jest programistą, często pracuje z telefonu.

## Kryteria auta (ustalone z użytkownikiem)
- Golf VII: roczniki 2013–2020, przedlift (7) i lift (7.5, od roku modelowego 2017).
- **Tylko 5-drzwiowy hatchback.** Odrzucamy: Variant/kombi, Alltrack, Sportsvan, 3-drzwiowe, e-Golf, GTE.
- Budżet **40 000 zł**; oferty do 44 000 zł przechodzą tylko z „do negocjacji”.
- Przebieg do 180 tys. km (7.5 dostaje +20 tys. km zapasu).
- **R-Line ma najwyższy priorytet** (pakiet stylistyczny, nie Golf R). „Wygląd R-Line”, naklejki, zderzaki → „do potwierdzenia”.
- Golf R (300 KM) za tę cenę = czerwona flaga, alert blokowany.
- Celem są „perełki” – auta wyraźnie tańsze od podobnych, a nie po prostu najtańsze.

## Jak działa
- `sniper/scrapers/otomoto.py` – OtoMoto: JSON z `__NEXT_DATA__` (urqlState) na liście + strona ogłoszenia (VIN, drzwi, ASO).
- `sniper/scrapers/olx.py` – publiczne API `olx.pl/api/v1/offers`; ogłoszenia przekierowujące na OtoMoto są pomijane.
- `sniper/parse.py` – normalizacja, rozpoznawanie wersji (R-Line, Highline…), liftu, filtry twarde.
- `sniper/scoring.py` – wycena (mediany w koszykach rocznik × przebieg × skrzynia × paliwo × R-Line; na start zgrubna tabela `PRIOR_BY_YEAR`) i wynik 0–100 z czerwonymi flagami.
- `sniper/db.py` – SQLite: ogłoszenia, historia cen, ustawienia z bota (tabela kv).
- `sniper/telegram.py` – alerty z przyciskami ⭐/🙈 i komendy (/status, /raport, /budzet, /przebieg, /tylko_rline, /pauza, /wznow, /ulubione, /ustawienia).
- `sniper/main.py` – tryby: `--scheduled` (GitHub Actions), `--once`, `--dry-run`, praca ciągła.
- Progi: 💎 perełka ≥80 (alert od razu), 🔥 okazja ≥65, raport 50–64 (podsumowanie o 20:00).

## Wdrożenie
- Działa na **GitHub Actions** (`.github/workflows/sniper.yml`), repo publiczne, cron co ~10–20 min. Użytkownik nie chce płacić za serwer.
- Baza `data/sniper.db` przechowywana między uruchomieniami przez `actions/cache` (restore/save).
- Sekrety w repo: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`. **Nigdy nie zapisuj tokenu w kodzie ani w commitach.**
- Komendy z Telegrama są obsługiwane na początku każdego skanu (opóźnienie do kilkunastu minut).
- Pierwsze uruchomienie 2026-11-04 przeszło i bot wysłał oferty.

## Do sprawdzenia / pomysły
- Czy OtoMoto nie blokuje serwerów GitHuba (sprawdź w logach kroku „Skan”: `OtoMoto strona 1: N ogłoszeń`).
  Po 5 nieudanych skanach z rzędu bot sam wysyła ostrzeżenie.
- Wycena kalibruje się po 2–3 tygodniach zbierania danych.
- Możliwe rozszerzenia: AutoScout24, hash zdjęć do wykrywania duplikatów, uczenie wag z kliknięć ⭐/🙈.

## Testy
`pip install -r requirements.txt && python -m pytest` – testy działają offline na przykładowych danych z portali.
