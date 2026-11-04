"""Autoplac.pl: strona wyników z danymi API zapisanymi w <script id="ng-state">."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from ..config import Settings
from ..models import Listing
from ..parse import enrich, to_int
from .base import Http, ScraperError

log = logging.getLogger("sniper.autoplac")

BASE = "https://autoplac.pl"
SEARCH = BASE + "/oferty/samochody-osobowe/volkswagen/golf/vii"
NG_RE = re.compile(r'<script id="ng-state" type="application/json">(.*?)</script>', re.S)
FUEL = {"GASOLINE": "benzyna", "DIESEL": "diesel", "HYBRID": "hybryda", "ELECTRIC": "elektryczny",
        "LPG": "benzyna", "GASOLINE_LPG": "benzyna"}


def search_params(s: Settings, market: bool) -> dict:
    p = {"orderBy": "INSERT_TIME", "sortOrder": "DESC"}
    if not market:
        p["priceTo"] = s.budget + s.budget_tolerance + 2000
    return p


def parse_offer(item: dict) -> Listing:
    o = item.get("offer") or item
    price = ((o.get("priceInfo") or {}).get("primary") or {}).get("price")
    kw = o.get("enginePowerKW")
    created = o.get("insertTime")
    version = o.get("version") if o.get("version") not in (None, "Inny") else ""
    photos = item.get("photoList") or []
    l = Listing(
        source="autoplac", listing_id=str(o.get("id")), url=BASE + (o.get("webUrl") or ""),
        title=o.get("title") or f"{o.get('brand', '')} {o.get('model', '')}",
        price_pln=to_int(price),
        year=to_int(o.get("productionYear")),
        mileage_km=to_int(o.get("mileage")),
        engine_cc=to_int(o.get("engineCapacity")),
        power_hp=round(kw * 1.36) if kw else None,
        fuel=FUEL.get(o.get("fuelType") or "", o.get("fuelTypeText")),
        gearbox=o.get("transmissionTypeText"),
        body=o.get("bodyTypeText") or o.get("bodyType"),
        country_origin="Polska" if o.get("vehicleOrigin") == "DOMESTIC" else None,
        seller_type="prywatny" if o.get("sellerType") == "PRIVATE" else "firma",
        location=", ".join(x for x in (o.get("city"), o.get("voivodeshipDisplay")) if x) or None,
        photo=photos[0].get("url") if photos else None,
        created_at=datetime.fromtimestamp(created / 1000, timezone.utc).isoformat() if created else None,
        extra={"model": "golf", "version": version or "", "generation": o.get("generation") or ""},
    )
    if o.get("vinAvailable"):
        l.extra["vin_available"] = True
    if (o.get("generation") or "").upper() not in ("", "VII"):
        l.title += f" [generacja {o.get('generation')}]"
    return enrich(l, version)


def parse_search(html: str) -> list[Listing]:
    m = NG_RE.search(html or "")
    if not m:
        raise ScraperError("Autoplac: brak ng-state – strona się zmieniła")
    ng = json.loads(m.group(1))
    key = next((k for k in ng if "/offers/search" in k), None)
    if not key:
        return []
    out = []
    for item in (ng[key].get("body") or {}).get("offerList") or []:
        try:
            out.append(parse_offer(item))
        except Exception as e:  # noqa: BLE001
            log.debug("Autoplac: pominięto ogłoszenie: %s", e)
    return out


class AutoplacScraper:
    name = "autoplac"
    sniper_pages, market_pages = 1, 1

    def __init__(self, settings: Settings):
        self.s = settings
        self.http = Http(min_delay=2.0, max_delay=4.0)

    def search(self, pages: int = 1, market: bool = False) -> list[Listing]:
        items = parse_search(self.http.get(SEARCH, search_params(self.s, market)) or "")
        log.info("Autoplac: %d ogłoszeń", len(items))
        return items

    def detail(self, l: Listing) -> Listing:
        return l
