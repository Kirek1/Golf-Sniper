"""Sprzedajemy.pl: lista z JSON-LD (tytuł, cena, link), parametry auta ze strony ogłoszenia."""
from __future__ import annotations

import html as htmllib
import json
import logging
import re

from ..config import Settings
from ..models import Listing
from ..parse import enrich, to_int
from .base import Http

log = logging.getLogger("sniper.sprzedajemy")

SEARCH = "https://sprzedajemy.pl/motoryzacja/samochody-osobowe/volkswagen/golf"
LD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
ID_RE = re.compile(r"-nr(\d+)")


def _ld_blocks(html: str) -> list:
    out = []
    for raw in LD_RE.findall(html or ""):
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def parse_search(html: str) -> list[Listing]:
    out = []
    for block in _ld_blocks(html):
        if block.get("@type") != "ItemList":
            continue
        for it in block.get("itemListElement") or []:
            url = it.get("url") or ""
            m = ID_RE.search(url)
            if not m or it.get("priceCurrency", "PLN") != "PLN":
                continue
            l = Listing(source="sprzedajemy", listing_id=m.group(1), url=url,
                        title=it.get("name") or "", price_pln=to_int(it.get("price")),
                        photo=it.get("image"), extra={"model": "golf", "version": ""})
            out.append(enrich(l))
    return out


PAIR_RE = re.compile(r"<span>\s*([^<]+?)\s*</span>\s*<strong[^>]*>(.*?)</strong>", re.S)
SELLER_RE = re.compile(r"seller-type-info--(\w+)")


def parse_detail(page: str, l: Listing) -> Listing:
    attrs = {}
    for k, v in PAIR_RE.findall(page or ""):
        v = htmllib.unescape(re.sub(r"<[^>]+>", " ", v))
        attrs.setdefault(k.strip().lower(), re.sub(r"\s+", " ", v).strip())

    l.year = to_int(attrs.get("rok produkcji") or attrs.get("rok pierwszej rejestracji")) or l.year
    l.mileage_km = to_int(attrs.get("przebieg")) or l.mileage_km
    l.engine_cc = to_int(attrs.get("pojemność silnika")) or l.engine_cc
    l.power_hp = to_int(attrs.get("moc")) or l.power_hp
    l.fuel = attrs.get("paliwo") or l.fuel
    l.gearbox = attrs.get("skrzynia biegów") or l.gearbox
    l.body = attrs.get("rodzaj nadwozia") or attrs.get("typ nadwozia") or l.body
    doors = attrs.get("liczba drzwi")
    if doors:
        l.doors = max((to_int(d) or 0) for d in doors.split("/")) or None
    if attrs.get("kraj pochodzenia"):
        l.country_origin = attrs["kraj pochodzenia"]
    if (attrs.get("stan techniczny") or "").lower().startswith("uszkodz"):
        l.description += " [portal: uszkodzony]"
    m = SELLER_RE.search(page or "")
    if m:
        l.seller_type = "prywatny" if m.group(1) == "private" else "firma"
    for block in _ld_blocks(page):
        if block.get("@type") in ("Vehicle", "Car", "Product") and block.get("description"):
            l.description = re.sub(r"\s+", " ", block["description"]).strip() + l.description
            break
    l.detail_loaded = True
    return enrich(l)


class SprzedajemyScraper:
    name = "sprzedajemy"
    sniper_pages, market_pages = 1, 1

    def __init__(self, settings: Settings):
        self.s = settings
        self.http = Http(min_delay=2.0, max_delay=4.0)

    def search(self, pages: int = 1, market: bool = False) -> list[Listing]:
        items = parse_search(self.http.get(SEARCH, {"sort": "inp_srt_date_d"}) or "")
        log.info("Sprzedajemy: %d ogłoszeń", len(items))
        return items

    def detail(self, l: Listing) -> Listing:
        page = self.http.get(l.url)
        if page is None:
            l.extra["removed"] = True
            return l
        return parse_detail(page, l)
