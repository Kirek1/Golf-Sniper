# Golf 7 Sniper 🚗

Skanuje OtoMoto i OLX co ~4 minuty. Szuka VW Golfa 7 / 7.5, 5-drzwiowy hatchback,
roczniki 2013–2020, do 40 000 zł (do 44 000 zł z dopiskiem „do negocjacji”). R-Line ma priorytet.
Każdą ofertę wycenia względem rynku i wysyła na Telegram tylko te, które warto sprawdzić.

## Uruchomienie na GitHubie (za darmo, bez włączonego komputera)

1. **Utwórz repozytorium publiczne** na github.com (New repository → Public).
   W publicznym repo GitHub Actions jest bez limitu minut. W prywatnym darmowe minuty
   skończą się po kilku dniach. Token i tak jest bezpieczny, bo trafia do Secrets, nie do kodu.
2. **Wgraj pliki:** rozpakuj zip, w repo kliknij *Add file → Upload files* i przeciągnij
   **całą zawartość** folderu `golf-sniper`, razem z folderem `.github`.
   Jeśli systemowo ukrywa Ci foldery z kropką, włącz pokazywanie ukrytych plików.
   Nie wgrywaj pliku `.env`.
3. **Chat ID:** napisz do swojego bota `/start`, potem otwórz w przeglądarce
   `https://api.telegram.org/bot<TWÓJ_TOKEN>/getUpdates`
   i znajdź `"chat":{"id":123456789` – ta liczba to Twój chat ID.
4. **Sekrety:** w repo *Settings → Secrets and variables → Actions → New repository secret*:
   - `TELEGRAM_TOKEN` – token od BotFather
   - `TELEGRAM_CHAT_ID` – liczba z punktu 3
5. **Start:** zakładka *Actions* → włącz workflows → *Golf 7 Sniper* → *Run workflow*.
   Po 1–3 minutach bot napisze na Telegramie, że działa. Od tej pory GitHub uruchamia skan
   sam, zwykle co 10–20 minut.

**Dobrze wiedzieć:**
- Komendy w bocie (`/budzet`, `/status`…) działają, ale odpowiedź przychodzi przy następnym
  skanie, czyli z opóźnieniem do kilkunastu minut.
- GitHub wyłącza harmonogram, gdy w publicznym repo przez 60 dni nic się nie dzieje.
  Dostaniesz wtedy maila. Wystarczy kliknąć *Enable workflow* albo zrobić dowolną zmianę w repo.
- Jeśli portal zablokuje serwery GitHuba, po 5 nieudanych skanach bot sam da znać.
  Logi każdego skanu są w zakładce *Actions*.

## Uruchomienie na własnym komputerze

1. **Python 3.11+** → w folderze projektu:
   ```
   pip install -r requirements.txt
   ```
2. **Bot na Telegramie:** napisz do `@BotFather`, wyślij `/newbot`, nadaj nazwę i skopiuj token.
3. Skopiuj `.env.example` jako `.env` i wklej token do `TELEGRAM_TOKEN`.
4. Napisz cokolwiek do swojego nowego bota, potem uruchom:
   ```
   python get_chat_id.py
   ```
   i wklej wynik do `TELEGRAM_CHAT_ID` w `.env`.
5. **Test bez Telegrama** (alerty wypiszą się w konsoli):
   ```
   python -m sniper.main --once --dry-run
   ```
6. **Praca ciągła:**
   ```
   python -m sniper.main
   ```
   Komputer musi być włączony. Żeby działało 24/7, postaw to na małym VPS albo Raspberry Pi:
   ```
   docker build -t golf-sniper .
   docker run -d --restart unless-stopped --env-file .env -v $(pwd)/data:/data golf-sniper
   ```

## Co przychodzi na Telegram

| Poziom | Wynik | Kiedy |
|---|---|---|
| 💎 Perełka | 80+ | od razu, z dźwiękiem, także przy /pauza |
| 🔥 Okazja | 65–79 | od razu |
| 👀 Do obejrzenia | 50–64 | tylko w dziennym raporcie o 20:00 |

Pod każdym alertem są przyciski ⭐ (do ulubionych) i 🙈 (ignoruj na zawsze).
Obniżka ceny o min. 1000 zł daje ponowny alert. To samo auto na dwóch portalach
(ten sam VIN albo rocznik + przebieg + cena) zgłaszane jest raz.

**Komendy:** `/status` `/raport` `/ulubione` `/budzet 40000` `/przebieg 180000`
`/tylko_rline on|off` `/pauza` `/wznow` `/ustawienia`

## Jak liczony jest wynik (0–100)

| Składnik | Pkt |
|---|---|
| Cena vs rynek (−12% = maks.) | 40 |
| Historia: VIN, salon PL, ASO, bezwypadkowy, 1. właściciel | 20 |
| Przebieg względem wieku | 15 |
| Wersja: R-Line, 7.5, DSG | 10 |
| Sprzedający prywatny | 10 |
| Świeżość / obniżka | 5 |

**Czerwone flagi:** cena ponad 25% poniżej rynku, brak VIN, bardzo niski przebieg bez historii,
VIN wystawiany wcześniej z wyższym przebiegiem, zaliczka, „auto za granicą”, Golf R za 40 tys.
Te trzy ostatnie blokują alert.

**Wycena:** na start aplikacja używa zgrubnej tabeli cen (`sniper/scoring.py → PRIOR_BY_YEAR`).
Co 6 godzin robi pełny skan rynku, także aut droższych niż budżet. Liczy z niego mediany
w koszykach: rocznik × przebieg × skrzynia × paliwo × R-Line. Po 2–3 tygodniach wycena
opiera się już na prawdziwych danych, a alert pokazuje jej pewność i liczbę podobnych aut.

## Gdy coś przestanie działać

Portale co jakiś czas zmieniają strony. Po 5 nieudanych skanach z rzędu bot sam napisze
„⚠️ scraper do poprawki”. Kod parsowania jest w `sniper/scrapers/otomoto.py` i `olx.py`.
Testy uruchomisz komendą `python -m pytest`.

Nie skracaj `POLL_INTERVAL` poniżej ~3 minut. Regulaminy portali nie przewidują
automatycznego pobierania, więc trzymaj małe tempo i używaj tego tylko do własnych poszukiwań.

## Przed telefonem do sprzedającego

- Sprawdź VIN na historiapojazdu.gov.pl (potrzebny VIN, numer rejestracyjny i data 1. rejestracji).
  Przy imporcie zrób dodatkowo płatny raport.
- Na oględziny weź miernik lakieru i diagnostykę OBD. Porównaj przebieg ze sterowników.
- Przy DSG (DQ200) zapytaj o wymianę oleju i mechatronikę.
- Prawdziwy R-Line rozpoznasz po kierownicy ze spłaszczonym dołem, sportowych fotelach,
  progach i logo R-Line na błotnikach.
- Nie wpłacaj zaliczki za auto, którego nie widziałeś.
