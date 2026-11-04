# Golf 7 Sniper – kontekst projektu

Bot, który szuka dla właściciela repo używanego **VW Golfa 7 / 7.5** i wysyła okazje na Telegram.
Rozmawiaj z użytkownikiem **po polsku**, prostym językiem – nie jest programistą, często pracuje z telefonu.

## Kryteria auta (ustalone z użytkownikiem)
- Golf VII: roczniki 2013–2020, przedlift (7) i lift (7.5, od roku modelowego 2017).
- **Tylko 5-drzwiowy hatchback.** Odrzucamy: Variant/kombi, Alltrack, Sportsvan, 3-drzwiowe, e-Golf, GTE.
- GTI i GTD są mile widziane (użytkownik prosił o nie wprost).
- Budżet **40 000 zł**; oferty do 44 000 zł przechodzą tylko z „do negocjacji”.
- Przebieg do 180 tys. km (7.5 dostaje +20 tys. km zapasu).
- **R-Line ma najwyższy priorytet** (pakiet stylistyczny, nie Golf R). „Wygląd R-Line”, naklejki, zderzaki → „do potwierdzenia”.
- Golf R (300 KM) za tę cenę = czerwona flaga, alert blokowany.
- Celem są „perełki” – auta wyraźnie tańsze od podobnych, a nie po prostu najtańsze.

## Jak działa
- `sniper/scrapers/otomoto.py` – OtoMoto: JSON z `__NEXT_DATA__` (urqlState) na liście + strona ogłoszenia (VIN, drzwi, ASO).
- `sniper/scrapers/olx.py` – strona wyników OLX, `window.__PRERENDERED_STATE__` (API /api/v1 blokuje VPS-y); kategoria Volkswagen + `filter_enum_model=golf`; ogłoszenia z OtoMoto (partner) pomijane.
- `sniper/scrapers/autoplac.py` – `<script id="ng-state">`, klucz z `/offers/search`, sortowanie `orderBy=INSERT_TIME&sortOrder=DESC`.
- `sniper/scrapers/sprzedajemy.py` – JSON-LD ItemList na liście, parametry `<span>Etykieta</span><strong>wartość</strong>` na stronie ogłoszenia.
- `sniper/scrapers/autoscout24.py` – import z Europy; cena € × kurs NBP + akcyza + ~3500 zł; nie wchodzi do wyceny rynku PL. `IMPORT=0` wyłącza.
- `sniper/scrapers/base.py` – `curl_cffi` z odciskiem Chrome (zwykły httpx dostaje 403).
- Facebook Marketplace (logowanie) i Allegro Lokalnie (antybot) świadomie pominięte; Gratka przekierowuje na motogratka.pl – nie sprawdzone.
- `sniper/parse.py` – normalizacja, rozpoznawanie wersji (R-Line, Highline…), liftu, filtry twarde.
- `sniper/scoring.py` – wycena (mediany w koszykach rocznik × przebieg × skrzynia × paliwo × R-Line; na start zgrubna tabela `PRIOR_BY_YEAR`) i wynik 0–100 z czerwonymi flagami.
- `sniper/db.py` – SQLite: ogłoszenia, historia cen, ustawienia z bota (tabela kv).
- `sniper/telegram.py` – alerty z przyciskami ⭐/🙈; komendy tylko: /start (opis czego szukamy), /status, /raport, /ulubione, /pauza, /wznow. Użytkownik chce mało komend.
- `sniper/main.py` – tryby: `--scheduled` (GitHub Actions), `--once`, `--dry-run`, praca ciągła.
- Progi: 💎 perełka ≥80 (alert od razu), 🔥 okazja ≥65, raport 50–64 (podsumowanie o 20:00).

## Wdrożenie
- Docelowo działa na **VPS-ie użytkownika** (OVH, Ubuntu, 4 GB RAM, na tym samym serwerze stoi jego strona kirekreps) jako usługa systemd `golf-sniper` w `~/golf-sniper`, z limitem `MemoryMax=300M` i `Nice=10`, żeby nie przeszkadzać stronie.
- Aktualizacja na VPS: `git pull && .venv/bin/pip install -r requirements.txt && sudo systemctl restart golf-sniper`.
- GitHub Actions (`.github/workflows/sniper.yml`) – tylko ręczne uruchomienie, harmonogram wyłączony (dwa boty = podwójne alerty).
- Sekrety w repo: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`. **Nigdy nie zapisuj tokenu w kodzie ani w commitach.**
- Komendy z Telegrama są obsługiwane na początku każdego skanu (opóźnienie do kilkunastu minut).
- 2026-11-04 (test z VPS): OtoMoto, OLX (strona wyników), Sprzedajemy i AutoScout24 działają; Autoplac zwraca 403. Http robi „rozgrzewkę” (wejście na stronę główną) i ponawia raz; po 3 blokadach z rzędu portal ma przerwę 24 h (`paused_sources` w kv) i jedną cichą informację na Telegramie.

## Do sprawdzenia / pomysły
- Po każdej zmianie sprawdź w logach (`journalctl -u golf-sniper`) linijki „<Portal> strona 1: N ogłoszeń” dla wszystkich źródeł.
  Po 5 nieudanych skanach z rzędu bot sam wysyła ostrzeżenie.
- Wycena kalibruje się po 2–3 tygodniach zbierania danych.
- Możliwe rozszerzenia: AutoScout24, hash zdjęć do wykrywania duplikatów, uczenie wag z kliknięć ⭐/🙈.

## Testy
`pip install -r requirements.txt && python -m pytest` – testy działają offline na przykładowych danych z portali.
