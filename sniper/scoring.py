"""Wycena rynkowa i deal score 0–100."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .config import Settings
from .models import Listing

# Punkt startowy, zanim aplikacja zbierze własne dane (PLN, 5d hatch, benzyna,
# ~150 tys. km, wersja podstawowa/Comfortline). To zgrubne założenie –
# po 2–3 tygodniach zbierania ofert przeważają mediany z bazy.
PRIOR_BY_YEAR = {2012: 30000, 2013: 32000, 2014: 35000, 2015: 38500, 2016: 42000,
                 2017: 48000, 2018: 53000, 2019: 58000, 2020: 64000}
TRIM_FACTOR = {"r-line": 1.08, "highline": 1.04, "gti": 1.18, "gtd": 1.12, "sound": 1.02,
               "join": 1.03, "lounge": 1.02, "iq.drive": 1.03, "comfortline": 1.0,
               "trendline": 0.94, "bluemotion": 0.95, "golf-r": 1.6}


def prior_price(l: Listing) -> int | None:
    if not l.year:
        return None
    p = PRIOR_BY_YEAR.get(min(max(l.year, 2012), 2020))
    km = l.mileage_km or 160000
    p *= max(0.7, min(1.25, 1 - 0.0012 * (km - 150000) / 1000))
    p *= TRIM_FACTOR.get(l.trim or "", 1.0)
    if l.rline == "do potwierdzenia" and l.trim != "r-line":
        p *= 1.02
    if l.gearbox == "automat":
        p *= 1.05
    if l.fuel == "diesel":
        p *= 0.97
    if l.facelift and l.year == 2016:
        p *= 1.06
    return int(round(p, -2))


def bucket(l: Listing, level: int) -> tuple:
    km = (l.mileage_km or 0) // 25000
    if level == 0:
        return (l.year, km, bool(l.facelift), l.gearbox, l.fuel, l.trim == "r-line")
    if level == 1:
        return (l.year, km // 2, bool(l.facelift), l.gearbox)
    return (l.year, bool(l.facelift))


class MarketModel:
    """Mediany cen w koszykach (rocznik × przebieg × skrzynia × paliwo × R-Line)."""

    def __init__(self, rows: list[Listing]):
        self.buckets: list[dict[tuple, list[int]]] = [{}, {}, {}]
        for l in rows:
            if not l.price_pln or not l.year or l.price_pln < 8000:
                continue
            for lvl in range(3):
                self.buckets[lvl].setdefault(bucket(l, lvl), []).append(l.price_pln)
        self.size = len(rows)

    def estimate(self, l: Listing) -> tuple[int | None, int, str]:
        """(cena oczekiwana, liczba podobnych aut, pewność: wysoka/średnia/niska)."""
        prior = prior_price(l)
        for lvl, need in ((0, 6), (1, 8), (2, 12)):
            prices = [p for p in self.buckets[lvl].get(bucket(l, lvl), [])
                      if p != l.price_pln]  # nie licz samego siebie
            if len(prices) >= need:
                med = statistics.median(prices)
                conf = "wysoka" if lvl == 0 and len(prices) >= 12 else "średnia"
                if lvl == 2 and prior:            # bardzo zgrubny koszyk – mieszaj z priorem
                    med = 0.5 * med + 0.5 * prior
                    conf = "niska"
                return int(round(med, -2)), len(prices), conf
        return prior, 0, "niska"


@dataclass
class Result:
    score: int
    expected: int | None
    diff_pct: float | None
    tier: str | None
    confidence: str
    similar: int
    parts: dict = field(default_factory=dict)
    plus: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    blocking: bool = False


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_listing(l: Listing, model: MarketModel, s: Settings, *,
                  price_drop: int | None = None, vin_history: list | None = None,
                  current_year: int = 2026) -> Result:
    expected, similar, conf = model.estimate(l)
    plus: list[str] = []
    flags: list[str] = []
    blocking = False

    # 1) cena vs rynek (40)
    diff = None
    price_pts = 0.0
    if expected and l.price_pln:
        diff = (expected - l.price_pln) / expected
        price_pts = 40 * _clamp(diff / 0.12 * 0.85 + 0.15) if diff >= 0 else 40 * _clamp(0.15 + diff * 1.5)
        if diff > 0.30:
            flags.append(f"cena {diff:.0%} poniżej rynku – możliwa szkoda, cofnięty licznik lub oszustwo")
            price_pts = 15
        elif diff > 0.25:
            flags.append(f"cena podejrzanie niska ({diff:.0%} poniżej rynku)")
            price_pts = 25

    # 2) przebieg vs wiek (15)
    km_pts = 7.5
    if l.mileage_km is not None and l.year:
        age = max(1, current_year - l.year)
        per_year = l.mileage_km / age
        km_pts = 15 * _clamp(1 - (per_year - 12000) / 18000)
        if per_year < 6000 and not (l.aso or l.vin):
            flags.append(f"bardzo niski przebieg ({per_year:,.0f} km/rok) bez historii – sprawdź licznik".replace(",", " "))
            km_pts = 6
    else:
        flags.append("brak przebiegu w ogłoszeniu")

    # 3) historia i dokumenty (20)
    hist = 0
    if l.vin:
        hist += 6; plus.append("VIN")
    elif l.detail_loaded:
        flags.append("brak VIN")
    if l.registered_pl or (l.country_origin or "").lower() in ("polska", "pl"):
        hist += 3
    if (l.country_origin or "").lower() in ("polska", "pl"):
        hist += 2; plus.append("salon PL")
    if l.aso:
        hist += 4; plus.append("ASO")
    if l.accident_free:
        hist += 3; plus.append("bezwypadkowy")
    if l.first_owner:
        hist += 2; plus.append("1. właściciel")
    hist_pts = min(20, hist)

    # 4) wersja i wyposażenie (10)
    eq = {"r-line": 6, "highline": 4, "gti": 5, "gtd": 4, "join": 3, "sound": 3, "lounge": 3,
          "iq.drive": 3, "comfortline": 2, "allstar": 2, "trendline": 0, "bluemotion": 0}.get(l.trim or "", 1)
    if l.rline == "do potwierdzenia":
        eq = max(eq, 4)
        flags.append("R-Line do potwierdzenia na zdjęciach")
    if l.facelift:
        eq += 2; plus.append("7.5")
    if l.gearbox == "automat":
        eq += 2
    eq_pts = min(10, eq)

    # 5) sprzedający (10)
    seller_pts = 5
    if l.seller_type == "prywatny":
        seller_pts = 9; plus.append("prywatny")
    elif l.seller_type == "firma":
        seller_pts = 5

    # 6) świeżość / obniżka (5)
    fresh_pts = 4
    if price_drop and price_drop > 0:
        fresh_pts = 5; plus.append(f"obniżka o {price_drop:,} zł".replace(",", " "))

    # --- czerwone flagi blokujące --------------------------------------
    if l.trim == "golf-r":
        flags.append("Golf R (300 KM) za tę cenę – prawie na pewno szkoda lub oszustwo")
        blocking = True
    text = l.text
    if any(w in text for w in ("zaliczk", "przedpłat", "wyślę auto", "auto jest za granicą",
                               "transport z", "kontakt tylko mailowy", "whatsapp")):
        flags.append("podejrzane warunki sprzedaży (zaliczka / auto za granicą / tylko e-mail)")
        blocking = True
    for other in vin_history or []:
        if other["mileage"] and l.mileage_km and other["mileage"] > l.mileage_km + 3000:
            flags.append(f"ten VIN był już wystawiany z większym przebiegiem ({other['mileage']:,} km)".replace(",", " "))
            blocking = True
            break
    eng = l.engine_cc or 0
    if l.fuel == "benzyna" and l.year and l.year <= 2014 and eng and eng < 1300:
        flags.append("1.2 TSI z wczesnych roczników – sprawdź rozrząd i zużycie oleju")

    total = price_pts + km_pts + hist_pts + eq_pts + seller_pts + fresh_pts
    total -= 4 * len([f for f in flags if "do potwierdzenia" not in f])
    score = int(round(_clamp(total, 0, 100)))
    if blocking:
        score = min(score, 45)

    tier = None
    if not blocking:
        if score >= s.tier_pearl:
            tier = "perelka"
        elif score >= s.tier_deal:
            tier = "okazja"
        elif score >= s.tier_report:
            tier = "raport"

    return Result(score=score, expected=expected, diff_pct=diff, tier=tier, confidence=conf,
                  similar=similar, plus=plus, flags=flags, blocking=blocking,
                  parts={"cena": round(price_pts), "przebieg": round(km_pts), "historia": hist_pts,
                         "wersja": eq_pts, "sprzedajacy": seller_pts, "swiezosc": fresh_pts})
