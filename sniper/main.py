"""Pętla główna: skanuj → filtruj → oceń → wyślij alert.

Uruchomienie:
    python -m sniper.main               # praca ciągła
    python -m sniper.main --once        # jeden przebieg
    python -m sniper.main --once --dry-run   # bez Telegrama, alerty w konsoli
"""
from __future__ import annotations

import argparse
import logging
import random
import time
from datetime import datetime

from .config import settings
from .db import DB, TIER_RANK
from .parse import enrich, hard_filter, is_golf7_hatch_5d
from .scoring import MarketModel, score_listing
from .scrapers.base import ScraperError
from .scrapers.olx import OlxScraper
from .scrapers.otomoto import OtomotoScraper
from .telegram import Telegram

log = logging.getLogger("sniper")


class Sniper:
    def __init__(self, dry_run: bool = False):
        self.s = settings
        self.db = DB(self.s.db_path)
        self.tg = Telegram(self.s, self.db, dry_run=dry_run)
        self.tg.load_saved_settings()
        self.tg.status_fn = self.status
        self.scrapers = [OtomotoScraper(self.s), OlxScraper(self.s)]
        saved_fail = self.db.kv_get("failures", {})
        self.failures = {sc.name: saved_fail.get(sc.name, 0) for sc in self.scrapers}
        self.last_ok: dict[str, str] = self.db.kv_get("last_ok", {})
        self.model = MarketModel(self.db.market_rows())
        self.last_market = float(self.db.kv_get("last_market", 0))
        self.last_report_day = self.db.kv_get("last_report_day")
        self.detail_budget = 10_000   # limit pobrań szczegółów na jedno uruchomienie

    def status(self) -> str:
        lines = [f"Wycena oparta na {self.model.size} ofertach"]
        for name, ts in self.last_ok.items():
            lines.append(f"{name}: ostatni skan {ts}")
        for name, n in self.failures.items():
            if n:
                lines.append(f"⚠️ {name}: {n} nieudanych skanów z rzędu")
        return "\n".join(lines)

    # --- jeden przebieg -----------------------------------------------
    def scan(self, market: bool = False) -> int:
        alerts = 0
        for sc in self.scrapers:
            try:
                items = sc.search(pages=5 if market else 2, market=market)
                self.failures[sc.name] = 0
                self.last_ok[sc.name] = datetime.now().strftime("%d.%m %H:%M")
            except (ScraperError, Exception) as e:  # noqa: BLE001
                self.failures[sc.name] += 1
                log.error("%s: %s", sc.name, e)
                if self.failures[sc.name] == 5:
                    self.tg.send(f"⚠️ {sc.name}: 5 nieudanych skanów z rzędu ({str(e)[:200]}). "
                                 "Portal zmienił stronę albo blokuje serwery GitHuba.")
                continue
            finally:
                self.db.kv_set("failures", self.failures)
                self.db.kv_set("last_ok", self.last_ok)
            for l in items:
                alerts += self.process(sc, l)
        return alerts

    def process(self, sc, l) -> int:
        golf7, _ = is_golf7_hatch_5d(l)
        l.extra["golf7_hatch"] = golf7
        passes, _ = hard_filter(l, self.s)
        known = self.db.get(l.key)

        # szczegóły (VIN, historia, drzwi) pobieramy tylko dla kandydatów, raz
        if passes and not l.detail_loaded and not self.db.has_detail(l.key) and self.detail_budget > 0:
            self.detail_budget -= 1
            try:
                l = sc.detail(l)
            except Exception as e:  # noqa: BLE001
                log.warning("Szczegóły %s: %s", l.url, e)
            golf7, _ = is_golf7_hatch_5d(l)
            l.extra["golf7_hatch"] = golf7
            passes, _ = hard_filter(l, self.s)
        elif known and self.db.has_detail(l.key):
            cached = self.db.listing(l.key)
            for f in ("vin", "doors", "body", "aso", "accident_free", "first_owner",
                      "registered_pl", "country_origin", "seller_type"):
                if getattr(cached, f) is not None and getattr(l, f) in (None, ""):
                    setattr(l, f, getattr(cached, f))
            if len(cached.description) > len(l.description):
                l.description = cached.description
            l.extra["version"] = l.extra.get("version") or cached.extra.get("version", "")
            l.detail_loaded = True
            enrich(l, l.extra.get("version"))
            passes, _ = hard_filter(l, self.s)

        info = self.db.upsert(l, passes)
        if not passes:
            return 0
        row = self.db.get(l.key)
        if row["user_status"] == "ignore":
            return 0

        drop = None
        if info["old_price"] and l.price_pln and l.price_pln < info["old_price"]:
            drop = info["old_price"] - l.price_pln
        r = score_listing(l, self.model, self.s, price_drop=drop,
                          vin_history=self.db.vin_history(l),
                          current_year=datetime.now().year)
        self.db.set_score(l.key, r.expected, r.score, r.tier)

        if r.tier not in ("perelka", "okazja"):
            return 0
        # alert: nowa oferta, wyższy poziom niż wcześniej albo obniżka ≥ 1000 zł od ostatniego alertu
        prev_tier, prev_price = row["alerted_tier"], row["alerted_price"]
        better_tier = TIER_RANK[r.tier] > TIER_RANK.get(prev_tier, 0)
        cheaper = prev_price and l.price_pln and prev_price - l.price_pln >= 1000
        if not (better_tier or cheaper):
            return 0
        if prev_tier is None and self.db.same_car_alerted(l):
            log.info("Duplikat z innego portalu: %s", l.url)
            return 0
        if self.tg.send_alert(l, r, price_drop=(prev_price - l.price_pln) if cheaper else drop):
            self.db.mark_alerted(l.key, r.tier, l.price_pln)
            log.info("ALERT %s %d/100 %s", r.tier, r.score, l.url)
            return 1
        return 0

    def refresh_market(self):
        log.info("Pełny skan rynku do wyceny (także auta powyżej budżetu)")
        self.scan(market=True)
        self.model = MarketModel(self.db.market_rows())
        self.db.mark_inactive(48)
        self.last_market = time.time()
        self.db.kv_set("last_market", self.last_market)
        log.info("Model wyceny: %d ofert", self.model.size)

    def maybe_daily_report(self):
        now = datetime.now()
        day = now.strftime("%Y-%m-%d")
        if now.hour >= self.s.daily_report_hour and self.last_report_day != day:
            self.tg.send(self.tg.report_text(), silent=True)
            self.last_report_day = day
            self.db.kv_set("last_report_day", day)

    def run_forever(self):
        self.tg.start_polling()
        self.tg.send("🚗 Golf 7 Sniper wystartował. /help – lista komend.", silent=True)
        while True:
            try:
                if time.time() - self.last_market > self.s.market_interval:
                    self.refresh_market()
                n = self.scan()
                log.info("Skan zakończony, alertów: %d", n)
                self.maybe_daily_report()
            except Exception:  # noqa: BLE001
                log.exception("Nieoczekiwany błąd w pętli")
            time.sleep(self.s.poll_interval + random.randint(-40, 60))


    def run_scheduled(self):
        """Jedno uruchomienie z harmonogramu (GitHub Actions): komendy → skan → raport → koniec."""
        self.detail_budget = 12                 # żeby jedno uruchomienie trwało kilka minut, nie godzinę
        try:
            if self.tg.process_updates_once():
                self.tg.load_saved_settings()   # np. nowy /budzet działa od razu
        except Exception as e:  # noqa: BLE001
            log.warning("Komendy Telegrama: %s", e)
        if not self.db.kv_get("welcomed"):
            self.tg.send("🚗 Golf 7 Sniper działa na GitHubie. /help – lista komend.\n"
                         "Komendy obsługuję przy każdym skanie, więc odpowiedź może przyjść po kilkunastu minutach.",
                         silent=True)
            self.db.kv_set("welcomed", True)
        if time.time() - self.last_market > self.s.market_interval:
            self.refresh_market()
        n = self.scan()
        log.info("Skan zakończony, alertów: %d", n)
        self.maybe_daily_report()


def main():
    ap = argparse.ArgumentParser(description="Golf 7 Sniper")
    ap.add_argument("--once", action="store_true", help="jeden przebieg i koniec")
    ap.add_argument("--scheduled", action="store_true",
                    help="jedno uruchomienie z harmonogramu (GitHub Actions)")
    ap.add_argument("--dry-run", action="store_true", help="alerty w konsoli zamiast Telegrama")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not a.dry_run and not (settings.telegram_token and settings.telegram_chat_id):
        missing = [n for n, v in (("TELEGRAM_TOKEN", settings.telegram_token),
                                  ("TELEGRAM_CHAT_ID", settings.telegram_chat_id)) if not v]
        raise SystemExit(f"BRAK: {', '.join(missing)}. Na GitHubie dodaj je w Settings → Secrets and "
                         "variables → Actions → zakładka Secrets → New repository secret. "
                         "Lokalnie wpisz je do pliku .env.")
    sn = Sniper(dry_run=a.dry_run)
    if a.scheduled:
        sn.run_scheduled()
    elif a.once:
        sn.refresh_market()
        print(f"Alertów: {sn.scan()}")
    else:
        sn.run_forever()


if __name__ == "__main__":
    main()
