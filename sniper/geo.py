"""Odległość od miasta użytkownika.

Współrzędne: z ogłoszenia (OLX podaje je wprost), a dla pozostałych portali z nazwy
miasta przez darmowy geokoder OpenStreetMap (Nominatim) – z pamięcią w bazie,
żeby każde miasto sprawdzać tylko raz (Nominatim pozwala na 1 zapytanie/s).
"""
from __future__ import annotations

import logging
import math
import time

import httpx

from .db import DB
from .models import Listing

log = logging.getLogger("sniper.geo")

ROAD_FACTOR = 1.25      # drogą jest zwykle ~25% dalej niż w linii prostej
_last_call = [0.0]


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


class Geo:
    def __init__(self, db: DB, home: str):
        self.db = db
        self.home_name = (home or "").strip()
        self.home = self.geocode(self.home_name) if self.home_name else None
        if self.home_name and not self.home:
            log.warning("Nie znalazłem miasta HOME_CITY=%s – odległości wyłączone", self.home_name)

    def geocode(self, place: str) -> tuple[float, float] | None:
        key = "geo:" + place.lower().strip()
        cached = self.db.kv_get(key)
        if cached is not None:
            return tuple(cached) if cached else None
        wait = _last_call[0] + 1.1 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()
        try:
            r = httpx.get("https://nominatim.openstreetmap.org/search",
                          params={"q": place, "format": "json", "limit": 1, "accept-language": "pl"},
                          headers={"User-Agent": "GolfSniper/1.0 (prywatny bot ogloszen)"}, timeout=15)
            hits = r.json()
            coords = (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None
        except Exception as e:  # noqa: BLE001 – brak odległości to nie powód, żeby przerywać skan
            log.debug("Geokodowanie %s: %s", place, e)
            return None
        self.db.kv_set(key, list(coords) if coords else [])
        return coords

    def distance_km(self, l: Listing) -> int | None:
        """Szacowana odległość drogą (km) albo None, gdy nie wiadomo."""
        if not self.home:
            return None
        if l.extra.get("lat") and l.extra.get("lon"):
            pt = (l.extra["lat"], l.extra["lon"])
        elif l.location:
            place = l.location
            if l.extra.get("import"):           # "Mülheim, DE"
                pt = self.geocode(place)
            else:
                pt = self.geocode(place + ", Polska")
            if not pt:
                return None
        else:
            return None
        return int(round(haversine_km(self.home, pt) * ROAD_FACTOR, -1))
