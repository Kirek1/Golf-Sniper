"""OLX: strona wyników z danymi w window.__PRERENDERED_STATE__ (API /api/v1 blokuje boty).

Kategoria Volkswagen + filtr model=golf, sortowanie od najnowszych.
Ogłoszenia, które są tak naprawdę z OtoMoto (partner/link zewnętrzny), pomijamy –
łapie je scraper OtoMoto.
"""
from __future__ import annotations

import json
import logging
import re

from ..config import Settings
from ..models import Listing
from ..parse import enrich, to_int
from .base import Http, ScraperError

log = logging.getLogger("sniper.olx")

SEARCH = "https://www.olx.pl/motoryzacja/samochody/volkswagen/"
STATE_RE = re.compile(r'window\.__PRERENDERED_STATE__\s*=\s*("(?:[^"\\]|\\.)*")\s*;', re.S)


def search_params(s: Settings, page: int, market: bool) -> dict:
    p = {
        "search[filter_enum_model][0]": "golf",
        "search[filter_float_year:from]": s.year_from,
        "search[filter_float_year:to]": s.year_to,
        "search[order]": "created_at:desc",
    }
    if not market:
        p["search[filter_float_price:to]"] = s.budget + s.budget_tolerance + 2000
    if page > 1:
        p["page"] = page
    return p


def _state(html: str) -> dict:
    m = STATE_RE.search(html or "")
    if not m:
        raise ScraperError("OLX: brak __PRERENDERED_STATE__ – strona się zmieniła albo zablokowała zapytanie")
    return json.loads(json.loads(m.group(1)))


def _is_otomoto(ad: dict) -> bool:
    partner = ad.get("partner") or {}
    blob = f"{ad.get('url', '')} {ad.get('externalUrl') or ''} {partner.get('code', '') if isinstance(partner, dict) else ''}"
    return "otomoto" in blob.lower()


def parse_ad(ad: dict) -> Listing:
    pm = {p.get("key"): p for p in ad.get("params") or []}

    def val(k):  # czytelna wartość, np. "169 000 km"
        return (pm.get(k) or {}).get("value")

    def norm(k):  # znormalizowana, np. "169000", "automatic"
        return (pm.get(k) or {}).get("normalizedValue")

    price = ad.get("price") or {}
    reg = price.get("regularPrice") or {}
    loc = ad.get("location") or {}
    photos = ad.get("photos") or []
    desc = re.sub(r"<[^>]+>", " ", ad.get("description") or "")
    l = Listing(
        source="olx", listing_id=str(ad.get("id")), url=(ad.get("url") or "").split("?")[0],
        title=ad.get("title") or "",
        price_pln=to_int(reg.get("value")),
        negotiable=bool(reg.get("negotiable")),
        year=to_int(norm("year") or val("year")),
        mileage_km=to_int(norm("milage") or val("milage")),
        engine_cc=to_int(norm("enginesize") or val("enginesize")),
        power_hp=to_int(norm("enginepower") or val("enginepower")),
        fuel=val("petrol") or norm("petrol"),
        gearbox=val("transmission") or norm("transmission"),
        body=val("car_body") or norm("car_body"),
        doors=to_int(norm("door_count") or val("door_count")),
        country_origin=val("country_origin"),
        seller_type="firma" if ad.get("isBusiness") else "prywatny",
        location=", ".join(x for x in (loc.get("cityName"), loc.get("regionName")) if x) or None,
        description=re.sub(r"\s+", " ", desc).strip(),
        photo=photos[0] if photos and isinstance(photos[0], str) else None,
        created_at=ad.get("createdTime"),
        detail_loaded=True,      # lista OLX zawiera pełny opis i parametry
        extra={"model": norm("model") or val("model") or "", "version": ""},
    )
    mp = ad.get("map") or {}
    if mp.get("lat") and mp.get("lon"):
        l.extra["lat"], l.extra["lon"] = float(mp["lat"]), float(mp["lon"])
    user = ad.get("user") or {}
    if user.get("id"):
        l.extra["seller_id"] = str(user["id"])
    vin = norm("vin") or val("vin")
    if vin:
        l.vin = str(vin).strip()
        l.extra["vin_available"] = True        # OLX często maskuje VIN ("xxxx…")
    cond = (norm("condition") or "").lower()
    if cond == "damaged":
        l.description += " [portal: uszkodzony]"
    elif cond == "notdamaged":
        l.accident_free = None   # "nieuszkodzony" ≠ "bezwypadkowy", nie dajemy punktów
    if (norm("righthanddrive") or "1") == "0":
        l.description += " [portal: kierownica po prawej]"
    model = (l.extra["model"] or "").lower()
    if model and model != "golf":          # golf-plus, golf-sportsvan, golf-variant…
        l.title = f"{l.title} [{model}]"
    return enrich(l)


def parse_search(html: str) -> list[Listing]:
    st = _state(html)
    ads = (((st.get("listing") or {}).get("listing") or {}).get("ads")) or []
    out = []
    for ad in ads:
        if _is_otomoto(ad):
            continue
        try:
            out.append(parse_ad(ad))
        except Exception as e:  # noqa: BLE001 – jedno dziwne ogłoszenie nie psuje całej strony
            log.debug("OLX: pominięto ogłoszenie %s: %s", ad.get("id"), e)
    return out


def parse_offers(payload: dict) -> list[Listing]:
    """Stary format z API /api/v1/offers (zostawiony jako zapas)."""
    out = []
    for o in payload.get("data") or []:
        if "otomoto" in (o.get("url") or ""):
            continue
        params = []
        for p in o.get("params") or []:
            v = p.get("value") or {}
            if p.get("key") == "price":
                continue
            params.append({"key": p.get("key"), "value": v.get("label"), "normalizedValue": v.get("key")})
        pr = next((p.get("value") for p in o.get("params") or [] if p.get("key") == "price"), {}) or {}
        ad = {"id": o.get("id"), "url": o.get("url"), "title": o.get("title"), "description": o.get("description"),
              "params": params, "isBusiness": o.get("business"), "createdTime": o.get("created_time"),
              "price": {"regularPrice": {"value": pr.get("value"), "negotiable": pr.get("negotiable")}},
              "location": {"cityName": ((o.get("location") or {}).get("city") or {}).get("name"),
                           "regionName": ((o.get("location") or {}).get("region") or {}).get("name")},
              "photos": [ph.get("link", "").replace("{width}", "640").replace("{height}", "480")
                         for ph in (o.get("photos") or [])[:1]]}
        out.append(parse_ad(ad))
    return out


B62 = "0123456789abcdefghijklmnopqrstuvwxyz" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def short_user_id(n: int) -> str:
    """Krótki identyfikator profilu w adresie /oferty/uzytkownik/<id>/ (base62 z numeru użytkownika)."""
    s = ""
    while n:
        n, r = divmod(n, 62)
        s = B62[r] + s
    return s or "0"


def parse_user_page(html: str) -> dict | None:
    """Profil sprzedawcy: czy OLX oznacza go jako firmę i ile ma ogłoszeń (w tym aut)."""
    m = STATE_RE.search(html or "")
    if not m:
        return None
    st = json.loads(json.loads(m.group(1)))
    ul = st.get("userListing") or {}
    seller = ((ul.get("seller") or {}).get("data")) or {}
    listing = ul.get("userListing") or {}
    ads = listing.get("ads") or []
    return {
        "is_business": bool(seller.get("is_business")),
        "ads_total": int(listing.get("totalElements") or len(ads)),
        "car_ads": sum(1 for a in ads if (a.get("category") or {}).get("type") == "automotive"),
    }


class OlxScraper:
    name = "olx"
    sniper_pages, market_pages = 2, 4

    def __init__(self, settings: Settings):
        self.s = settings
        self.http = Http(min_delay=2.0, max_delay=5.0)

    def search(self, pages: int = 1, market: bool = False) -> list[Listing]:
        out: list[Listing] = []
        for page in range(1, pages + 1):
            html = self.http.get(SEARCH, search_params(self.s, page, market))
            items = parse_search(html or "")
            log.info("OLX strona %d: %d ogłoszeń", page, len(items))
            out.extend(items)
            if not items:
                break
        return out

    def detail(self, l: Listing) -> Listing:
        return l

    def seller_info(self, l: Listing) -> dict | None:
        """Ile ogłoszeń ma "prywatny" sprzedawca. 3+ auta = najpewniej handlarz."""
        uid = l.extra.get("seller_id")
        if not uid:
            return None
        html = self.http.get(f"https://www.olx.pl/oferty/uzytkownik/{short_user_id(int(uid))}/")
        info = parse_user_page(html or "")
        if info is None:                      # nietypowy identyfikator – weź link ze strony ogłoszenia
            page = self.http.get(l.url) or ""
            m = re.search(r"/oferty/uzytkownik/([A-Za-z0-9]+)/", page)
            if m:
                info = parse_user_page(self.http.get(f"https://www.olx.pl/oferty/uzytkownik/{m.group(1)}/") or "")
        return info
