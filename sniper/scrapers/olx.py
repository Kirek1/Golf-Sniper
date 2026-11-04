"""OLX: publiczne API wyszukiwania, z którego korzysta strona olx.pl."""
from __future__ import annotations

import logging
import re

from ..config import Settings
from ..models import Listing
from ..parse import enrich, to_int
from .base import Http

log = logging.getLogger("sniper.olx")

API = "https://www.olx.pl/api/v1/offers/"
CARS_CATEGORY = 84          # Motoryzacja > Samochody osobowe


def search_params(s: Settings, offset: int, market: bool) -> dict:
    p = {
        "offset": offset, "limit": 40, "category_id": CARS_CATEGORY,
        "query": "volkswagen golf", "sort_by": "created_at:desc",
        "filter_float_year:from": s.year_from, "filter_float_year:to": s.year_to,
    }
    if not market:
        p["filter_float_price:to"] = s.budget + s.budget_tolerance + 2000
    return p


def _params(offer: dict) -> dict:
    out = {}
    for p in offer.get("params") or []:
        v = p.get("value") or {}
        out[p.get("key")] = v
    return out


def parse_offers(payload: dict) -> list[Listing]:
    out = []
    for o in payload.get("data") or []:
        url = o.get("url") or ""
        if "otomoto.pl" in url:      # to samo ogłoszenie łapie scraper OtoMoto
            continue
        pm = _params(o)
        price = pm.get("price") or {}
        label = lambda k: (pm.get(k) or {}).get("label") or (pm.get(k) or {}).get("key")  # noqa: E731
        desc = re.sub(r"<[^>]+>", " ", o.get("description") or "")
        loc = o.get("location") or {}
        photos = o.get("photos") or []
        photo = None
        if photos:
            photo = photos[0].get("link", "").replace("{width}", "640").replace("{height}", "480")
        l = Listing(
            source="olx", listing_id=str(o.get("id")), url=url, title=o.get("title") or "",
            price_pln=to_int(price.get("value")),
            negotiable=bool(price.get("negotiable")),
            year=to_int(label("year")),
            mileage_km=to_int(label("milage") or label("mileage")),
            engine_cc=to_int(label("enginesize")),
            power_hp=to_int(label("enginepower")),
            fuel=label("petrol"),
            gearbox=label("transmission"),
            body=label("car_body"),
            doors=to_int(label("door_count")),
            country_origin=label("country_origin"),
            seller_type="firma" if o.get("business") else "prywatny",
            location=", ".join(x for x in ((loc.get("city") or {}).get("name"),
                                           (loc.get("region") or {}).get("name")) if x) or None,
            description=re.sub(r"\s+", " ", desc).strip(),
            photo=photo, created_at=o.get("created_time"),
            detail_loaded=True,       # API od razu daje pełny opis
            extra={"model": label("model") or "", "version": ""},
        )
        if (label("condition") or "").lower().startswith("uszk"):
            l.description += " [portal: uszkodzony]"
        enrich(l)
        out.append(l)
    return out


class OlxScraper:
    name = "olx"

    def __init__(self, settings: Settings):
        self.s = settings
        self.http = Http(min_delay=2.0, max_delay=5.0)

    def search(self, pages: int = 1, market: bool = False) -> list[Listing]:
        out: list[Listing] = []
        for page in range(pages):
            payload = self.http.get(API, search_params(self.s, page * 40, market), as_json=True) or {}
            items = parse_offers(payload)
            log.info("OLX strona %d: %d ogłoszeń", page + 1, len(items))
            out.extend(items)
            if not payload.get("data"):
                break
        return out

    def detail(self, l: Listing) -> Listing:
        return l
