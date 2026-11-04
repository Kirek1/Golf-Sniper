"""OtoMoto: lista wyników (__NEXT_DATA__ / GraphQL w urqlState) + strona ogłoszenia."""
from __future__ import annotations

import json
import logging
import re

from ..config import Settings
from ..models import Listing
from ..parse import enrich, to_int
from .base import Http, ScraperError, next_data, walk

log = logging.getLogger("sniper.otomoto")

BASE = "https://www.otomoto.pl/osobowe/volkswagen/golf/od-2013"


def search_params(s: Settings, page: int, market: bool) -> dict:
    p = {
        "search[filter_enum_generation]": "gen-vii-2012",
        "search[filter_float_year:to]": str(s.year_to),
        "search[order]": "created_at_first:desc",
        "search[advanced_search_expanded]": "true",
    }
    if not market:  # tryb snajpera: tylko budżet z zapasem
        p["search[filter_float_price:to]"] = str(s.budget + s.budget_tolerance + 2000)
        p["search[filter_float_mileage:to]"] = str(s.max_mileage + 40000)
    if page > 1:
        p["page"] = str(page)
    return p


def _param_map(node: dict) -> dict:
    out = {}
    for p in node.get("parameters") or []:
        k = p.get("key")
        if k:
            out[k] = p.get("value") if p.get("value") not in (None, "") else p.get("displayValue")
            out[k + "__label"] = p.get("displayValue")
    return out


def _search_data(html: str):
    """Tylko wpis z wynikami (advertSearch). Wpis z filtrami ma ~700 KB i nie jest potrzebny."""
    data = next_data(html)
    urql = ((data.get("props") or {}).get("pageProps") or {}).get("urqlState")
    if isinstance(urql, dict):
        for v in urql.values():
            raw = v.get("data") if isinstance(v, dict) else None
            if isinstance(raw, str) and '"advertSearch"' in raw:
                return json.loads(raw)
    return data                       # inny układ strony – przeszukaj całość


def parse_search(html: str) -> list[Listing]:
    data = _search_data(html)
    seen, out = set(), []
    for d in walk(data):
        url = d.get("url")
        if not (isinstance(url, str) and "/oferta/" in url and isinstance(d.get("parameters"), list)):
            continue
        m = re.search(r"ID(\w+)\.html", url)
        lid = str(d.get("id") or (m.group(1) if m else url))
        if lid in seen:
            continue
        seen.add(lid)
        pm = _param_map(d)
        price = d.get("price") or {}
        amount = (price.get("amount") or {}).get("units") if isinstance(price.get("amount"), dict) else price.get("value")
        loc = d.get("location") or {}
        city = ((loc.get("city") or {}).get("name") if isinstance(loc.get("city"), dict) else loc.get("city"))
        region = ((loc.get("region") or {}).get("name") if isinstance(loc.get("region"), dict) else loc.get("region"))
        thumb = d.get("thumbnail") or {}
        l = Listing(
            source="otomoto", listing_id=lid, url=url, title=d.get("title") or "",
            price_pln=to_int(amount),
            year=to_int(pm.get("year") or pm.get("first_registration_year")),
            mileage_km=to_int(pm.get("mileage")),
            engine_cc=to_int(pm.get("engine_capacity")),
            power_hp=to_int(pm.get("engine_power")),
            fuel=pm.get("fuel_type__label") or pm.get("fuel_type"),
            gearbox=pm.get("gearbox__label") or pm.get("gearbox"),
            location=", ".join(x for x in (city, region) if x) or None,
            description=d.get("shortDescription") or "",
            photo=thumb.get("x2") or thumb.get("x1") if isinstance(thumb, dict) else None,
            created_at=d.get("createdAt"),
            extra={"version": pm.get("version__label") or "", "model": pm.get("model") or "golf"},
        )
        seller = d.get("sellerLink") or d.get("seller") or {}
        if isinstance(seller, dict):
            st = str(seller.get("type") or seller.get("__typename") or "").lower()
            l.seller_type = "firma" if ("dealer" in st or "professional" in st or "business" in st) else \
                ("prywatny" if "private" in st or "privateseller" in st else None)
        enrich(l, pm.get("version__label"))
        out.append(l)
    return out


YES = {"tak", "yes", "1", "true"}


def parse_detail(html: str, l: Listing) -> Listing:
    data = next_data(html)
    advert = None
    for d in walk(data):
        if isinstance(d.get("details"), list) and ("description" in d or "price" in d):
            advert = d
            break
    if advert is None:
        raise ScraperError("Nie znaleziono danych ogłoszenia OtoMoto")
    det = {}
    for x in advert.get("details") or []:
        if isinstance(x, dict) and x.get("key"):
            det[x["key"]] = str(x.get("value") or x.get("label") or "")
    desc = re.sub(r"<[^>]+>", " ", advert.get("description") or "")
    l.description = re.sub(r"\s+", " ", desc).strip() or l.description
    l.vin = det.get("vin") or l.vin
    l.body = det.get("body_type") or l.body
    l.doors = to_int(det.get("door_count")) or l.doors
    l.country_origin = det.get("country_origin") or l.country_origin
    if "no_accident" in det:
        l.accident_free = det["no_accident"].lower() in YES
    if det.get("damaged", "").lower() in YES:
        l.accident_free = False
        l.description += " [portal: uszkodzony]"
    if "service_record" in det:
        l.aso = det["service_record"].lower() in YES
    if "original_owner" in det:
        l.first_owner = det["original_owner"].lower() in YES
    if "registered" in det:
        l.registered_pl = det["registered"].lower() in YES
    seller = advert.get("seller") or {}
    st = str(seller.get("type") or "").lower()
    if st:
        l.seller_type = "firma" if st in ("business", "professional", "dealer") else "prywatny"
    l.extra["version"] = det.get("version") or l.extra.get("version", "")
    l.detail_loaded = True
    return enrich(l, l.extra.get("version"))


class OtomotoScraper:
    name = "otomoto"
    sniper_pages, market_pages = 2, 5

    def __init__(self, settings: Settings):
        self.s = settings
        self.http = Http()

    def search(self, pages: int = 1, market: bool = False) -> list[Listing]:
        out: list[Listing] = []
        for page in range(1, pages + 1):
            html = self.http.get(BASE, search_params(self.s, page, market))
            items = parse_search(html or "")
            log.info("OtoMoto strona %d: %d ogłoszeń", page, len(items))
            out.extend(items)
            if not items:
                break
        return out

    def detail(self, l: Listing) -> Listing:
        html = self.http.get(l.url)
        if html is None:
            l.extra["removed"] = True
            return l
        return parse_detail(html, l)
