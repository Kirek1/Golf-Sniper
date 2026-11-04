"""Wspólny format ogłoszenia – każdy scraper zwraca Listing."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Listing:
    source: str                     # otomoto / olx
    listing_id: str
    url: str
    title: str
    price_pln: int | None = None
    negotiable: bool = False
    year: int | None = None
    mileage_km: int | None = None
    engine_cc: int | None = None
    power_hp: int | None = None
    fuel: str | None = None         # benzyna / diesel / hybryda / elektryczny
    gearbox: str | None = None      # manual / automat
    body: str | None = None         # surowa wartość z portalu
    doors: int | None = None
    vin: str | None = None
    country_origin: str | None = None
    accident_free: bool | None = None
    aso: bool | None = None
    first_owner: bool | None = None
    registered_pl: bool | None = None
    seller_type: str | None = None  # prywatny / firma
    location: str | None = None
    description: str = ""
    photo: str | None = None
    created_at: str | None = None
    # wyliczane przez parse.enrich()
    trim: str | None = None         # r-line / highline / comfortline / gti ...
    rline: str = "nie"              # tak / do potwierdzenia / nie
    facelift: bool | None = None    # True = 7.5
    detail_loaded: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.listing_id}"

    @property
    def text(self) -> str:
        return f"{self.title} {self.description}".lower()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Listing":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
