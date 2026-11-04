"""Normalizacja danych, rozpoznawanie wersji i filtry twarde."""
from __future__ import annotations

import re

from .config import Settings
from .models import Listing


def to_int(value) -> int | None:
    """'189 000 km' -> 189000, '1 395 cm3' -> 1395, 150 -> 150."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d[\d \u00a0]*", str(value))
    if not m:
        return None
    return int(re.sub(r"\D", "", m.group(0)))


def norm_fuel(v: str | None) -> str | None:
    if not v:
        return None
    v = v.lower()
    if "hybr" in v:
        return "hybryda"
    if "elektr" in v or v == "electric":
        return "elektryczny"
    if "diesel" in v or "olej" in v:
        return "diesel"
    if "benz" in v or "petrol" in v or "lpg" in v:
        return "benzyna"
    return v


def norm_gearbox(v: str | None) -> str | None:
    if not v:
        return None
    v = v.lower()
    if "autom" in v or "dsg" in v:
        return "automat"
    if "manual" in v:
        return "manual"
    return v


# --- wersje wyposażenia -------------------------------------------------

RLINE_RE = re.compile(r"\br[\s\-_]?line\b", re.I)
RLINE_COSMETIC_RE = re.compile(
    r"(wygl[aą]d|stylizacj|optyk|naklejk|zderzak\w*\s+(w\s+stylu\s+)?r[\s\-]?line|look|"
    r"w stylu r[\s\-]?line|pod r[\s\-]?line|replik)", re.I)
GOLF_R_RE = re.compile(r"\bgolf\s*(vii|7(\.5)?)?\s*r\b(?![\s\-]?line)", re.I)

TRIMS = [  # kolejność = priorytet rozpoznania
    ("e-golf", re.compile(r"\be[\s\-]?golf\b", re.I)),
    ("gte", re.compile(r"\bgte\b", re.I)),
    ("gti", re.compile(r"\bgti\b", re.I)),
    ("gtd", re.compile(r"\bgtd\b", re.I)),
    ("highline", re.compile(r"\bhigh[\s\-]?line\b", re.I)),
    ("comfortline", re.compile(r"\bcomfort[\s\-]?line\b", re.I)),
    ("trendline", re.compile(r"\btrend[\s\-]?line\b", re.I)),
    ("join", re.compile(r"\bjoin\b", re.I)),
    ("sound", re.compile(r"\bsound\b", re.I)),
    ("lounge", re.compile(r"\blounge\b", re.I)),
    ("allstar", re.compile(r"\ball[\s\-]?star\b", re.I)),
    ("iq.drive", re.compile(r"\biq\.?\s?drive\b", re.I)),
    ("bluemotion", re.compile(r"\bblue[\s\-]?motion\b", re.I)),
]


def detect_rline(title: str, description: str, params_version: str | None = None) -> str:
    blob = f"{title} {params_version or ''}"
    full = f"{blob} {description}"
    if not RLINE_RE.search(full):
        return "nie"
    # samo "wygląd R-Line", "zderzaki R-Line", "naklejki" -> do potwierdzenia
    if RLINE_COSMETIC_RE.search(full):
        return "do potwierdzenia"
    if RLINE_RE.search(blob):
        return "tak"
    return "do potwierdzenia"   # tylko w opisie


def detect_trim(l: Listing, params_version: str | None = None) -> str | None:
    text = f"{l.title} {params_version or ''} {l.description}"
    if (l.power_hp or 0) >= 280 or GOLF_R_RE.search(f"{l.title} {params_version or ''}"):
        if (l.power_hp or 0) >= 280 or (l.power_hp is None and "4motion" in text.lower()):
            return "golf-r"
    for name, rx in TRIMS:
        if rx.search(f"{l.title} {params_version or ''}"):
            return name
    for name, rx in TRIMS:
        if rx.search(text):
            return name
    return None


FACELIFT_RE = re.compile(r"(7[\.,]5|\blift\b|facelift|po\s?lift|polift|\bvii\s*lift)", re.I)
PRE_LIFT_RE = re.compile(r"(przed\s?lift|przedlift|pre[\s\-]?lift)", re.I)


def detect_facelift(l: Listing) -> bool | None:
    if PRE_LIFT_RE.search(l.text):
        return False
    if l.year is None:
        return True if FACELIFT_RE.search(l.title) else None
    if l.year >= 2017:
        return True
    if l.year == 2016 and FACELIFT_RE.search(l.text):   # rocznik modelowy 2017 rejestrowany w 2016
        return True
    return False


def enrich(l: Listing, params_version: str | None = None) -> Listing:
    l.fuel = norm_fuel(l.fuel)
    l.gearbox = norm_gearbox(l.gearbox)
    if l.gearbox is None and re.search(r"\bdsg\b", l.text):
        l.gearbox = "automat"
    l.rline = detect_rline(l.title, l.description, params_version)
    l.trim = "r-line" if l.rline == "tak" else detect_trim(l, params_version)
    if l.trim == "r-line" and (l.power_hp or 0) >= 280:
        l.trim = "golf-r"
    l.facelift = detect_facelift(l)
    if l.vin:
        l.vin = l.vin.strip().upper()
        # OLX/Sprzedajemy pokazują zamaskowany VIN ("xxxxxxxx…") – to nie jest prawdziwy numer
        if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", l.vin) or len(set(l.vin)) < 6:
            l.vin = None
    if not l.vin:
        m = re.search(r"\b(WVWZZZ[A-HJ-NPR-Z0-9]{11})\b", l.description.upper())
        if m:
            l.vin = m.group(1)
    if l.doors is None:
        if re.search(r"\b3[\s\-]?(d|drzw)", l.title.lower()):
            l.doors = 3
        elif re.search(r"\b5[\s\-]?(d|drzw)", l.title.lower()):
            l.doors = 5
    return l


# --- filtry twarde ------------------------------------------------------

NOT_HATCH_RE = re.compile(r"\b(variant|kombi|alltrack|sportsvan|sports\s?van|kabrio|cabrio|plus)\b", re.I)
NEGATED_RE = re.compile(r"(nie\s?uszkodz\w*|bez\s+uszkodze\w*|nigdy\s+nie\s+by\w+\s+(uszkodz|zalan)\w*|nie\s?zalan\w*|nie\s?anglik)", re.I)
NOT_GOLF7_RE = re.compile(r"\bgolf\s*(iv|v|vi|viii)\b|\bgolf\s*[4568](?![\.,]5)(?!\d)(?!\s*-?\s*(d\b|p\b|drzw|porte|t\b|türer|motion))", re.I)


def is_golf7_hatch_5d(l: Listing) -> tuple[bool, str]:
    title = l.title.lower()
    if "golf" not in title and "golf" not in (l.extra.get("model") or "").lower():
        return False, "to nie Golf"
    if NOT_GOLF7_RE.search(l.title):
        return False, "inna generacja"
    if l.year and not (2012 <= l.year <= 2021):
        return False, "rocznik poza Golfem 7"
    body = (l.body or "").lower()
    if any(b in body for b in ("kombi", "estate", "minivan", "kabrio", "cabrio", "suv")):
        return False, "nadwozie nie hatchback"
    if NOT_HATCH_RE.search(l.title) or NOT_HATCH_RE.search(l.extra.get("version") or ""):
        return False, "nadwozie nie hatchback"
    if l.doors is not None and l.doors < 4:
        return False, "3 drzwi"
    if l.trim in ("e-golf", "gte"):
        return False, "e-Golf / GTE"
    return True, ""


def hard_filter(l: Listing, s: Settings) -> tuple[bool, str]:
    ok, why = is_golf7_hatch_5d(l)
    if not ok:
        return ok, why
    if l.year and not (s.year_from <= l.year <= s.year_to):
        return False, "rocznik"
    if l.price_pln is None:
        return False, "brak ceny"
    limit = s.budget + (s.budget_tolerance if l.negotiable else 0)
    if l.price_pln > limit:
        return False, "cena"
    if l.price_pln < 8000:
        return False, "cena nierealna (część/wynajem/rata)"
    if l.mileage_km is not None:
        cap = s.max_mileage + (20000 if l.facelift else 0)   # 7.5 przepuszczamy szerzej
        if l.mileage_km > cap:
            return False, "przebieg"
    # "nieuszkodzony", "bez uszkodzeń" nie mogą wykluczać
    text = NEGATED_RE.sub(" ", l.text)
    for w in s.exclude_words:
        if w in text:
            return False, f"słowo: {w}"
    if s.only_rline and l.rline == "nie":
        return False, "brak R-Line"
    return True, ""
