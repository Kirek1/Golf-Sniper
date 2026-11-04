"""AutoScout24: auta z Niemiec i reszty Europy (import).

Cena w EUR jest przeliczana po kursie NBP i powiększana o szacowany koszt
sprowadzenia (akcyza 3,1% + transport, tłumaczenia, badanie, rejestracja),
żeby porównywać ją z budżetem jak polskie oferty.
"""
from __future__ import annotations

import json
import logging
import re
import time

import httpx

from ..config import Settings
from ..models import Listing
from ..parse import enrich, to_int
from .base import Http, ScraperError

log = logging.getLogger("sniper.autoscout24")

BASE = "https://www.autoscout24.pl"
SEARCH = BASE + "/lst/volkswagen/golf"
NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

IMPORT_FIXED_PLN = 3500      # transport/laweta, tłumaczenia, badanie, rejestracja – zgrubnie
EXCISE = 0.031               # akcyza dla silników do 2.0 l

_rate = {"value": 4.25, "ts": 0.0}


def eur_rate() -> float:
    """Średni kurs EUR z NBP, odświeżany raz na 12 h (przy błędzie ostatni znany)."""
    if time.time() - _rate["ts"] > 12 * 3600:
        try:
            r = httpx.get("https://api.nbp.pl/api/exchangerates/rates/a/eur/?format=json", timeout=10)
            _rate["value"] = float(r.json()["rates"][0]["mid"])
        except Exception as e:  # noqa: BLE001
            log.warning("Kurs EUR z NBP niedostępny (%s), używam %.2f", e, _rate["value"])
        _rate["ts"] = time.time()
    return _rate["value"]


def import_cost_pln(price_eur: float, rate: float, engine_cc: int | None) -> int:
    pln = price_eur * rate
    excise = 0.186 if (engine_cc or 0) > 2000 else EXCISE
    return int(round(pln * (1 + excise) + IMPORT_FIXED_PLN, -2))


def search_params(s: Settings, page: int, market: bool, rate: float) -> dict:
    p = {"fregfrom": s.year_from, "fregto": s.year_to, "sort": "age", "desc": 1, "atype": "C",
         "ustate": "N,U"}
    if not market:
        p["priceto"] = int((s.budget + s.budget_tolerance) / rate / (1 + EXCISE))
    if page > 1:
        p["page"] = page
    return p


def parse_listing(a: dict, rate: float) -> Listing:
    v = a.get("vehicle") or {}
    tr = a.get("tracking") or {}
    loc = a.get("location") or {}
    seller = a.get("seller") or {}
    price_eur = (a.get("price") or {}).get("priceRaw") or to_int(tr.get("price"))
    reg = tr.get("firstRegistration") or ""          # "01-2017"
    year = to_int(reg.split("-")[-1]) if reg else None
    power_hp = None
    for d in a.get("vehicleDetails") or []:
        m = re.search(r"\((\d+)\s*KM\)", d.get("data") or "")
        if m:
            power_hp = int(m.group(1))
    cc = to_int(v.get("engineDisplacementInCCM"))
    # kolejność ważna: "Golf" + "VI 1.6 TDI…" daje "Golf VI", więc filtr generacji to złapie
    version = " ".join(x for x in (v.get("variant") or "Golf", v.get("modelVersionInput"), v.get("motorTypeName")) if x)
    title = f"{v.get('make', 'Volkswagen')} {version}".strip()
    country = loc.get("countryCode") or ""
    l = Listing(
        source="autoscout24", listing_id=str(a.get("id")), url=BASE + (a.get("url") or ""),
        title=title,
        price_pln=import_cost_pln(price_eur, rate, cc) if price_eur else None,
        year=year,
        mileage_km=to_int(tr.get("mileage") or v.get("mileageInKm")),
        engine_cc=cc, power_hp=power_hp,
        fuel=v.get("fuel"), gearbox=v.get("transmission"),
        country_origin={"DE": "Niemcy", "NL": "Holandia", "BE": "Belgia", "AT": "Austria",
                        "FR": "Francja", "IT": "Włochy", "ES": "Hiszpania", "LU": "Luksemburg"}.get(country, country),
        seller_type="prywatny" if "private" in str(seller.get("type", "")).lower() else "firma",   # PrivateSeller / Dealer
        location=", ".join(x for x in (loc.get("city"), country) if x) or None,
        photo=(a.get("images") or [None])[0],
        extra={"model": "golf", "version": version, "price_eur": price_eur, "eur_rate": rate, "import": True},
    )
    if v.get("isCurrentlyDamaged"):
        l.description += " [portal: uszkodzony]"
    return enrich(l, version)


def parse_search(html: str, rate: float) -> list[Listing]:
    m = NEXT_RE.search(html or "")
    if not m:
        raise ScraperError("AutoScout24: brak __NEXT_DATA__ – strona się zmieniła")
    pp = (json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}
    out = []
    for a in pp.get("listings") or []:
        try:
            out.append(parse_listing(a, rate))
        except Exception as e:  # noqa: BLE001
            log.debug("AutoScout24: pominięto ogłoszenie: %s", e)
    return out


class AutoScout24Scraper:
    name = "autoscout24"
    sniper_pages, market_pages = 1, 2

    def __init__(self, settings: Settings):
        self.s = settings
        self.http = Http(min_delay=2.5, max_delay=5.0)

    def search(self, pages: int = 1, market: bool = False) -> list[Listing]:
        rate = eur_rate()
        out: list[Listing] = []
        for page in range(1, pages + 1):
            items = parse_search(self.http.get(SEARCH, search_params(self.s, page, market, rate)) or "", rate)
            log.info("AutoScout24 strona %d: %d ogłoszeń", page, len(items))
            out.extend(items)
            if not items:
                break
        return out

    def detail(self, l: Listing) -> Listing:
        return l
