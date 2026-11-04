import json

from sniper.config import Settings
from sniper.db import DB
from sniper.models import Listing
from sniper.parse import enrich, hard_filter, to_int
from sniper.scoring import MarketModel, score_listing
from sniper.scrapers.olx import parse_offers
from sniper.scrapers.otomoto import parse_detail, parse_search
from sniper.telegram import Telegram, format_alert

S = Settings()


def car(**kw) -> Listing:
    base = dict(source="otomoto", listing_id="1", url="https://www.otomoto.pl/osobowe/oferta/x-ID1.html",
                title="Volkswagen Golf VII 1.5 TSI R-Line DSG", price_pln=38900, year=2018,
                mileage_km=142000, fuel="Benzyna", gearbox="Automatyczna", power_hp=150,
                engine_cc=1498, seller_type="prywatny", vin="WVWZZZAUZJW123456",
                country_origin="Polska", aso=True, accident_free=True, detail_loaded=True,
                description="Auto z polskiego salonu, serwisowane w ASO, bezwypadkowe, nieuszkodzone.")
    base.update(kw)
    return enrich(Listing(**base))


# --- parsowanie ---------------------------------------------------------

def test_to_int():
    assert to_int("189 000 km") == 189000
    assert to_int("39 900,00 PLN") == 39900
    assert to_int("1 395 cm3") == 1395
    assert to_int(150) == 150
    assert to_int(None) is None


def test_rline_detection():
    assert car().rline == "tak" and car().trim == "r-line"
    assert car(title="Golf 7 1.4 TSI Comfortline", description="wygląd R-Line, zderzaki R-Line").rline == "do potwierdzenia"
    assert car(title="Golf 7 1.6 TDI Highline", description="").rline == "nie"
    assert car(title="Golf 7 1.6 TDI Highline", description="").trim == "highline"
    assert car(title="Golf VII R 2.0 TSI 4Motion", power_hp=300, description="").trim == "golf-r"


def test_facelift():
    assert car(year=2018).facelift is True
    assert car(year=2015, description="").facelift is False
    assert car(year=2016, title="Golf 7.5 1.5 TSI R-Line").facelift is True


def test_hard_filter_body_and_doors():
    assert hard_filter(car(), S)[0]
    assert not hard_filter(car(title="Golf VII Variant 2.0 TDI R-Line"), S)[0]
    assert not hard_filter(car(title="Golf Sportsvan 1.4 TSI"), S)[0]
    assert not hard_filter(car(doors=3), S)[0]
    assert not hard_filter(car(title="Golf 7 1.4 TSI 3d R-Line", doors=None), S)[0]
    assert not hard_filter(car(title="Golf VI 1.4 TSI"), S)[0]
    assert hard_filter(car(title="Golf 5 drzwi 1.4 TSI R-Line"), S)[0]


def test_hard_filter_price_mileage_words():
    assert not hard_filter(car(price_pln=41500), S)[0]
    assert hard_filter(car(price_pln=41500, negotiable=True), S)[0]
    assert not hard_filter(car(year=2015, mileage_km=195000, description=""), S)[0]
    assert hard_filter(car(year=2018, mileage_km=195000), S)[0]        # 7.5 ma zapas
    assert not hard_filter(car(description="lekko uszkodzony przód"), S)[0]
    assert hard_filter(car(description="nieuszkodzony, bez uszkodzeń"), S)[0]
    assert not hard_filter(car(description="anglik przekładka"), S)[0]


# --- ocena --------------------------------------------------------------

def market(n=15, price=44500, **kw):
    return MarketModel([car(listing_id=str(100 + i), price_pln=price + (i - n // 2) * 300, **kw)
                        for i in range(n)])


def test_pearl_scores_high():
    r = score_listing(car(), market(), S, current_year=2026)
    assert r.expected and 43000 < r.expected < 46000
    assert r.tier == "perelka", r
    assert r.score >= 80


def test_market_price_offer_not_alerted():
    r = score_listing(car(price_pln=44500), market(), S, current_year=2026)
    assert r.tier not in ("perelka", "okazja")


def test_suspicious_cheap_is_flagged():
    r = score_listing(car(price_pln=27000), market(), S, current_year=2026)
    assert any("poniżej rynku" in f for f in r.flags)
    assert r.tier != "perelka"


def test_golf_r_blocked():
    l = car(title="Golf 7 R 2.0 TSI 4Motion", power_hp=310, description="")
    r = score_listing(l, market(), S, current_year=2026)
    assert r.blocking and r.tier is None


def test_scam_words_blocked():
    r = score_listing(car(description="auto jest za granicą, proszę o zaliczkę"), market(), S)
    assert r.blocking


def test_prior_used_without_data():
    r = score_listing(car(), MarketModel([]), S, current_year=2026)
    assert r.expected and r.confidence == "niska"


# --- scrapery na przykładowych danych ----------------------------------

def _next(payload) -> str:
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></html>'


OTOMOTO_NODE = {
    "id": "6140000001", "title": "Volkswagen Golf 1.5 TSI BMT R-Line DSG",
    "url": "https://www.otomoto.pl/osobowe/oferta/volkswagen-golf-ID6Hxyz.html",
    "shortDescription": "Salon PL, ASO",
    "createdAt": "2026-11-04T10:00:00Z",
    "price": {"amount": {"units": 39500, "currencyCode": "PLN"}},
    "location": {"city": {"name": "Kraków"}, "region": {"name": "Małopolskie"}},
    "parameters": [
        {"key": "make", "displayValue": "Volkswagen", "value": "volkswagen"},
        {"key": "model", "displayValue": "Golf", "value": "golf"},
        {"key": "year", "displayValue": "2018", "value": "2018"},
        {"key": "mileage", "displayValue": "139 000 km", "value": "139000"},
        {"key": "engine_capacity", "displayValue": "1 498 cm3", "value": "1498"},
        {"key": "engine_power", "displayValue": "150 KM", "value": "150"},
        {"key": "fuel_type", "displayValue": "Benzyna", "value": "petrol"},
        {"key": "gearbox", "displayValue": "Automatyczna", "value": "automatic"},
        {"key": "version", "displayValue": "1.5 TSI BMT R-Line DSG", "value": "ver-1-5"},
    ],
}


def test_otomoto_search_from_urql_state():
    inner = json.dumps({"advertSearch": {"edges": [{"node": OTOMOTO_NODE}]}})
    page = {"props": {"pageProps": {"urqlState": {"abc": {"data": inner}}}}}
    items = parse_search(_next(page))
    assert len(items) == 1
    l = items[0]
    assert (l.price_pln, l.year, l.mileage_km, l.gearbox, l.fuel) == (39500, 2018, 139000, "automat", "benzyna")
    assert l.rline == "tak" and l.facelift
    assert l.location == "Kraków, Małopolskie"


def test_otomoto_detail():
    l = parse_search(_next({"props": {"pageProps": {"urqlState": {"a": {"data": json.dumps(
        {"edges": [{"node": OTOMOTO_NODE}]})}}}}}))[0]
    detail = {"props": {"pageProps": {"advert": {
        "description": "<p>Bezwypadkowy, pierwszy właściciel</p>",
        "price": {"value": "39500"}, "seller": {"type": "PRIVATE"},
        "details": [{"key": "vin", "value": "WVWZZZAUZJW765432"}, {"key": "door_count", "value": "5"},
                    {"key": "body_type", "value": "Kompakt"}, {"key": "no_accident", "value": "Tak"},
                    {"key": "service_record", "value": "Tak"}, {"key": "country_origin", "value": "Polska"}]}}}}
    l = parse_detail(_next(detail), l)
    assert l.vin == "WVWZZZAUZJW765432" and l.doors == 5 and l.accident_free and l.aso
    assert l.seller_type == "prywatny" and l.detail_loaded
    assert hard_filter(l, S)[0]


def test_olx_parse_and_skip_otomoto():
    payload = {"data": [
        {"id": 900001, "url": "https://www.olx.pl/d/oferta/golf-7-r-line-CID5-IDabc.html",
         "title": "Golf 7 R-Line 1.4 TSI 2015", "description": "<p>Zadbany, 5 drzwi</p>",
         "business": False, "created_time": "2026-11-04T09:00:00+01:00",
         "location": {"city": {"name": "Poznań"}, "region": {"name": "Wielkopolskie"}},
         "photos": [{"link": "https://ireland.apollo.olxcdn.com/v1/files/x/image;s={width}x{height}"}],
         "params": [
             {"key": "price", "value": {"value": 36900, "currency": "PLN", "negotiable": True}},
             {"key": "model", "value": {"key": "golf", "label": "Golf"}},
             {"key": "year", "value": {"key": "2015", "label": "2015"}},
             {"key": "milage", "value": {"key": "148000", "label": "148 000 km"}},
             {"key": "petrol", "value": {"key": "petrol", "label": "Benzyna"}},
             {"key": "car_body", "value": {"key": "hatchback", "label": "Hatchback"}},
             {"key": "transmission", "value": {"key": "manual", "label": "Manualna"}}]},
        {"id": 900002, "url": "https://www.otomoto.pl/osobowe/oferta/x-ID9.html", "title": "Golf", "params": []},
    ]}
    items = parse_offers(payload)
    assert len(items) == 1
    l = items[0]
    assert (l.price_pln, l.negotiable, l.year, l.mileage_km) == (36900, True, 2015, 148000)
    assert l.seller_type == "prywatny" and l.rline == "tak" and l.facelift is False
    assert "640x480" in l.photo


# --- telegram i baza ----------------------------------------------------

def test_alert_format_and_commands(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    s = Settings()
    tg = Telegram(s, db, dry_run=True)
    l = car()
    r = score_listing(l, market(), s, current_year=2026)
    text = format_alert(l, r)
    assert "PEREŁKA" in text and "38 900 zł" in text and "Golf 7.5 R-Line" in text
    start = tg.command("/start", "")
    for word in ("R-Line", "GTI", "GTD", "Highline", "Trendline", "7.5", "hatchback", "OLX", "OtoMoto"):
        assert word in start, word
    assert "Nie znam" in tg.command("/budzet", "42000")
    assert "wstrzymane" in tg.command("/pauza", "") and tg.paused


def test_db_price_drop_and_duplicates(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    l = car()
    assert db.upsert(l, True)["new"]
    l.price_pln = 37500
    info = db.upsert(l, True)
    assert info["old_price"] == 38900
    assert len(db.price_history(l.key)) == 2
    db.mark_alerted(l.key, "perelka", 37500)
    twin = car(source="olx", listing_id="55", price_pln=37000)
    db.upsert(twin, True)
    assert db.same_car_alerted(twin)          # ten sam VIN na OLX = bez drugiego alertu


def test_scheduled_run_offline(tmp_path, monkeypatch):
    """Jedno uruchomienie z harmonogramu na podstawionych scraperach (bez sieci)."""
    from sniper import main as m
    monkeypatch.setattr(m.settings, "db_path", str(tmp_path / "s.db"))
    sn = m.Sniper(dry_run=True)

    class Fake:
        def __init__(self, name, items): self.name, self.items = name, items
        def search(self, pages=1, market=False): return self.items
        def detail(self, l): return l

    class Broken(Fake):
        def search(self, pages=1, market=False): raise RuntimeError("403")

    pool = [car(listing_id=str(200 + i), price_pln=44500 + (i - 7) * 300, vin=None) for i in range(15)]
    sn.scrapers = [Fake("otomoto", pool + [car(listing_id="999")]), Broken("olx", [])]
    sn.failures = {"otomoto": 0, "olx": 0}
    sn.run_scheduled()
    row = sn.db.get("otomoto:999")
    assert row["alerted_tier"] == "perelka"
    assert sn.db.kv_get("failures")["olx"] == 2          # skan rynku + zwykły skan
    sn2 = m.Sniper(dry_run=True)                         # stan przetrwał "restart"
    assert sn2.last_market > 0 and sn2.failures["olx"] == 2


# --- nowe źródła: próbki na podstawie prawdziwych stron (04.11.2026) ----

OLX_AD = {
    "id": 1109964626, "title": "Volkswagen Golf Auto z Gwarancją/Bezwypadek/DSG/PDC",
    "url": "https://www.olx.pl/d/oferta/volkswagen-golf-auto-z-gwarancja-bezwypadek-dsg-pdc-CID5-ID1d7iwg.html",
    "description": "Do sprzedania posiadam sprowadzonego z Niemiec Volkswagen Golf 2013 R-Line",
    "isBusiness": False, "createdTime": "2026-11-04T20:46:44+01:00", "partner": None, "externalUrl": None,
    "price": {"displayValue": "32 999 zł", "regularPrice": {"value": 32999, "currencyCode": "PLN", "negotiable": False}},
    "location": {"cityName": "Będzin", "regionName": "Śląskie"},
    "photos": ["https://ireland.apollo.olxcdn.com:443/v1/files/vo4jrh4x2exu2-PL/image;s=2048x946"],
    "params": [
        {"key": "vin", "value": "xxxxxxxxxxxxxxxxx", "normalizedValue": "xxxxxxxxxxxxxxxxx"},
        {"key": "model", "value": "Golf", "normalizedValue": "golf"},
        {"key": "year", "value": "2013", "normalizedValue": "2013"},
        {"key": "petrol", "value": "Benzyna", "normalizedValue": "petrol"},
        {"key": "car_body", "value": "Hatchback", "normalizedValue": "hatchback"},
        {"key": "enginesize", "value": "1 395 cm³", "normalizedValue": "1395"},
        {"key": "condition", "value": "Nieuszkodzony", "normalizedValue": "notdamaged"},
        {"key": "transmission", "value": "Automatyczna", "normalizedValue": "automatic"},
        {"key": "country_origin", "value": "Niemcy", "normalizedValue": "d"},
        {"key": "enginepower", "value": "122 KM", "normalizedValue": "122"},
        {"key": "milage", "value": "169 000 km", "normalizedValue": "169000"},
        {"key": "righthanddrive", "value": "po lewej", "normalizedValue": "1"}],
}


def _olx_html(ads):
    state = json.dumps({"listing": {"listing": {"ads": ads}}})
    return f"<script>window.__PRERENDERED_STATE__= {json.dumps(state)};</script>"


def test_olx_prerendered_state():
    from sniper.scrapers import olx
    sportsvan = json.loads(json.dumps(OLX_AD)); sportsvan["id"] = 2
    sportsvan["params"][1] = {"key": "model", "value": "Golf Sportsvan", "normalizedValue": "golf-sportsvan"}
    from_otomoto = dict(OLX_AD, id=3, partner={"code": "otomoto_pl"})
    items = olx.parse_search(_olx_html([OLX_AD, sportsvan, from_otomoto]))
    assert [i.listing_id for i in items] == ["1109964626", "2"]
    l = items[0]
    assert (l.price_pln, l.year, l.mileage_km, l.engine_cc, l.power_hp, l.gearbox) == (32999, 2013, 169000, 1395, 122, "automat")
    assert l.vin is None and l.extra["vin_available"]          # zamaskowany VIN
    assert l.location == "Będzin, Śląskie" and l.seller_type == "prywatny"
    assert hard_filter(l, S)[0]
    assert not hard_filter(items[1], S)[0]                     # Sportsvan odpada


def test_autoplac_ng_state():
    from sniper.scrapers import autoplac
    offer = {"offer": {"sellerType": "PRIVATE", "id": 4796143, "brand": "Volkswagen", "model": "Golf",
                       "generation": "VII", "version": "Inny", "productionYear": 2016, "mileage": 140000,
                       "enginePowerKW": 81, "engineCapacity": 1197, "bodyType": "HATCHBACK", "bodyTypeText": "Hatchback",
                       "fuelType": "GASOLINE", "vinAvailable": True, "transmissionTypeText": "Manualna",
                       "priceInfo": {"primary": {"price": 36900}}, "city": "Zduńska Wola",
                       "voivodeshipDisplay": "Łódzkie", "vehicleOrigin": "DOMESTIC",
                       "title": "Volkswagen Golf VII 1.2 VW 7 2016 r Salon Polska", "insertTime": 1791965908432,
                       "webUrl": "/oferta/volkswagen/golf/vii-1-2-2016r-vw-7-2016-r-salon-polska-8510-5560-YQ"},
             "photoList": [{"url": "https://euw2-cdn.autoplac.pl/v1/p/x?f=1920:webp"}]}
    ng = {"https://api.autoplac.pl/offers/search?orderBy=INSERT_TIME&seoCategories=samochody-osobowe,volkswagen,golf,vii":
          {"body": {"offerList": [offer], "offerCount": 1}, "status": 200}}
    html = f'<script id="ng-state" type="application/json">{json.dumps(ng)}</script>'
    items = autoplac.parse_search(html)
    l = items[0]
    assert l.url.startswith("https://autoplac.pl/oferta/") and l.price_pln == 36900
    assert (l.year, l.mileage_km, l.power_hp, l.fuel, l.gearbox) == (2016, 140000, 110, "benzyna", "manual")
    assert l.country_origin == "Polska" and l.seller_type == "prywatny" and l.extra["vin_available"]
    assert hard_filter(l, S)[0]


SPRZ_LIST = """<script type="application/ld+json">{"@context":"https://schema.org","@type":"ItemList","itemListElement":[
 {"@type":["ListItem","Offer"],"name":"Volkswagen Golf VI","url":"https://sprzedajemy.pl/volkswagen-golf-vi-olesno-4-6bea9f-nr73992989","price":19000,"priceCurrency":"PLN"},
 {"@type":["ListItem","Offer"],"name":"Volkswagen Golf 7 R-Line 1.4 TSI","url":"https://sprzedajemy.pl/volkswagen-golf-7-r-line-krakow-nr72783213","price":37900,"priceCurrency":"PLN"}]}</script>"""

SPRZ_DETAIL = """<ul><li class="item"> <span>Stan</span> <strong>używane </strong> </li>
<li class="item"> <span>Rok pierwszej rejestracji</span> <strong>2016 </strong> </li>
<li class="item"> <span>Rok produkcji</span> <strong>2016 </strong> </li>
<li class="item"> <span>Przebieg</span> <strong>142000 km</strong> </li>
<li class="item"> <span>Rodzaj nadwozia</span> <strong>Hatchback </strong> </li>
<li class="item"> <span>Pojemność silnika</span> <strong>1400 cm³</strong> </li>
<li class="item"> <span>Moc</span> <strong>125 KM</strong> </li>
<li class="item"> <span>Paliwo</span> <strong>benzyna </strong> </li>
<li class="item"> <span>Liczba drzwi</span> <strong>4/5 </strong> </li>
<li class="item"> <span>Stan techniczny</span> <strong>nieuszkodzony </strong> </li>
<li class="item"> <span>Skrzynia biegów</span> <strong>manualna </strong> </li></ul>
<div class="seller-type-info seller-type-info--private"><span class="seller-type-info__label">OSOBA PRYWATNA</span></div>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Vehicle","description":"Golf 7 R-Line, serwis ASO"}</script>"""


def test_sprzedajemy_list_and_detail():
    from sniper.scrapers import sprzedajemy
    items = sprzedajemy.parse_search(SPRZ_LIST)
    assert [i.listing_id for i in items] == ["73992989", "72783213"]
    assert not hard_filter(items[0], S)[0]                     # Golf VI odpada od razu po tytule
    l = items[1]
    assert hard_filter(l, S)[0] and l.year is None             # rocznik dopiero ze szczegółów
    l = sprzedajemy.parse_detail(SPRZ_DETAIL, l)
    assert (l.year, l.mileage_km, l.engine_cc, l.power_hp, l.doors, l.fuel, l.gearbox) == \
        (2016, 142000, 1400, 125, 5, "benzyna", "manual")
    assert l.seller_type == "prywatny" and "ASO" in l.description and l.body == "Hatchback"
    assert hard_filter(l, S)[0]


def test_autoscout24_import_price():
    from sniper.scrapers import autoscout24 as a24
    listing = {"id": "8e645f21-b7c0-4824-9738-0b0cf9cfa37d", "price": {"priceRaw": 8950},
               "url": "/oferta/volkswagen-golf-allstar-diesel-czarny-cat_ma74mo2084-8e645f21",
               "vehicle": {"make": "Volkswagen", "model": "Golf", "variant": "Golf", "motorTypeName": "1.6 TDI",
                           "modelVersionInput": "Allstar BMT PDC", "transmission": "Manualna", "fuel": "Diesel",
                           "mileageInKm": "194 975 km", "engineDisplacementInCCM": "1 598 cm³", "isCurrentlyDamaged": False},
               "location": {"countryCode": "DE", "city": "Mülheim"}, "seller": {"type": "Dealer"},
               "tracking": {"firstRegistration": "01-2017", "mileage": "194975", "price": "8950"},
               "vehicleDetails": [{"data": "81 kW (110 KM)"}]}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps({"props": {"pageProps": {"listings": [listing]}}})}</script>'
    l = a24.parse_search(html, 4.25)[0]
    assert l.price_pln == a24.import_cost_pln(8950, 4.25, 1598) == 42700
    assert (l.year, l.mileage_km, l.power_hp, l.country_origin, l.seller_type) == (2017, 194975, 110, "Niemcy", "firma")
    assert l.trim == "allstar" and l.extra["import"] and l.facelift
    r = score_listing(l, market(), S, current_year=2026)
    assert any("import" in f for f in r.flags)
    assert not hard_filter(l, S)[0]                            # 42 700 zł > 40 000 zł → odpada


# --- prawdziwe strony zapisane 04.11.2026 (regresja: jeśli parser się zepsuje, to wyjdzie tutaj) ----
import pathlib

FIX = pathlib.Path(__file__).parent / "fixtures"


def test_real_olx_page():
    from sniper.scrapers import olx
    ads = json.loads((FIX / "olx_ads_real.json").read_text(encoding="utf-8"))
    items = olx.parse_search(_olx_html(ads))
    assert len(ads) > 40 and len(items) >= 10                       # część to ogłoszenia OtoMoto – pomijane
    assert all(i.price_pln and i.year for i in items)
    reasons = {hard_filter(i, S)[1] for i in items}
    assert "nadwozie nie hatchback" in reasons                     # kombi wycięte
    assert any(hard_filter(i, S)[0] for i in items)


def test_real_autoplac_page():
    from sniper.scrapers import autoplac
    ng = json.loads((FIX / "autoplac_ng_real.json").read_text(encoding="utf-8"))
    items = autoplac.parse_search(f'<script id="ng-state" type="application/json">{json.dumps(ng)}</script>')
    assert len(items) >= 20 and all(i.price_pln and i.year and i.mileage_km for i in items)
    assert any(hard_filter(i, S)[0] for i in items)


def test_blocked_source_pauses_for_a_day(tmp_path, monkeypatch):
    from sniper import main as m
    from sniper.scrapers.base import BlockedError
    monkeypatch.setattr(m.settings, "db_path", str(tmp_path / "b.db"))
    sn = m.Sniper(dry_run=True)
    calls = {"n": 0}

    class Blocked:
        name = "autoplac"
        def search(self, pages=1, market=False):
            calls["n"] += 1
            raise BlockedError("HTTP 403")
        def detail(self, l): return l

    sn.scrapers = [Blocked()]
    sn.failures = {"autoplac": 0}
    for _ in range(5):
        sn.scan()
    assert calls["n"] == 3                                  # po 3 blokadach – przerwa, dalej nie pyta
    assert sn.db.kv_get("paused_sources")["autoplac"] > 0
    assert sn.db.kv_get("blocked_told_autoplac")           # jedna informacja na Telegramie


def test_raport_lists_all_and_splits(tmp_path):
    db = DB(str(tmp_path / "r.db"))
    tg = Telegram(Settings(), db, dry_run=True)
    for i in range(45):
        l = car(listing_id=str(i), price_pln=33000 + i * 100)
        db.upsert(l, True)
        db.set_score(l.key, 40000, 90 - i, "okazja" if i < 5 else "raport")
    sent = []
    tg._call = lambda method, **p: sent.append(p.get("text", ""))
    tg.send(tg.command("/raport", ""))
    assert len(sent) >= 2 and all(len(t) <= 4096 for t in sent)
    assert "(40)" in sent[0] and "🔥" in sent[0]
