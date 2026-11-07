"""Ściąga przed oględzinami – dopasowana do silnika, skrzyni i wersji konkretnego Golfa.

Zalecenia, a nie diagnozy: rzeczy, o które warto zapytać i które warto sprawdzić.
"""
from __future__ import annotations

from .models import Listing


def _engine(l: Listing) -> str:
    cc = l.engine_cc or 0
    hp = l.power_hp or 0
    t = l.text
    if l.fuel == "diesel" or (not l.fuel and "tdi" in t):
        return "tdi"
    if l.trim == "gti" or hp >= 220:
        return "gti"
    if cc:                                   # pojemność z ogłoszenia jest pewniejsza niż tytuł
        if 950 <= cc <= 1050:
            return "1.0"
        if 1450 <= cc <= 1550:
            return "1.5"
        if 1150 <= cc < 1450:
            return "1.2-1.4"
        return ""
    for key, name in (("1.0 tsi", "1.0"), ("1.5 tsi", "1.5"), ("1.4 tsi", "1.2-1.4"), ("1.2 tsi", "1.2-1.4")):
        if key in t:
            return name
    return ""


def checklist(l: Listing) -> str:
    eng = _engine(l)
    dsg = l.gearbox == "automat"
    wet_dsg = dsg and (eng == "gti" or l.trim == "gtd" or (eng == "tdi" and (l.engine_cc or 0) >= 1900))
    out: list[str] = []

    out.append("<b>Zawsze</b>")
    out.append("• VIN → historiapojazdu.gov.pl (przebiegi z przeglądów, szkody; potrzebny też nr rej. i data 1. rejestracji)")
    if l.extra.get("import") or (l.country_origin or "").lower() not in ("", "polska", "pl"):
        out.append("• import: płatny raport z kraju pochodzenia, komplet dokumentów (umowa/faktura, dowód)")
    out.append("• miernik lakieru na każdym elemencie – duże różnice = malowane")
    out.append("• diagnostyka OBD/VCDS: błędy i przebieg zapisany w sterownikach vs licznik")
    out.append("• książka serwisowa / faktury, ile kluczyków")
    out.append("• jazda próbna na zimnym silniku")

    out.append("\n<b>Silnik</b>")
    if eng == "tdi":
        out += ["• kiedy wymieniany pasek rozrządu z pompą wody (faktura)",
                "• DPF: czy auto jeździło głównie po mieście, czy kontrolka nie świeci, ile sadzy wg diagnostyki",
                "• EGR / chłodnica EGR – szarpanie, spadki mocy, ubytek płynu chłodzącego"]
        if not dsg:
            out.append("• dwumasa: stuki/wibracje na biegu jałowym i przy gaszeniu silnika")
    elif eng == "gti":
        out += ["• wycieki płynu przy module pompy wody / termostatu (częsta usterka 2.0 TSI)",
                "• zużycie oleju i czy auto nie było mocno tuningowane (chip, wydech, ślady wyczynowej jazdy)",
                "• kiedy wymieniany łańcuch rozrządu i czy nie grzechocze na zimnym"]
    elif eng == "1.5":
        out += ["• szarpanie przy ruszaniu i na niskich obrotach („kangurowanie”) – w ASO była aktualizacja oprogramowania, zapytaj czy zrobiona",
                "• wycieki płynu przy obudowie termostatu / pompy wody"]
    elif eng == "1.2-1.4":
        out += ["• pasek rozrządu: kiedy wymieniany (wielu mechaników zaleca ok. 150–210 tys. km albo co kilka lat)",
                "• wycieki płynu przy plastikowej obudowie termostatu / pompy wody",
                "• czy nie ubywa oleju między wymianami"]
        if (l.power_hp or 0) >= 145:
            out.append("• 1.4 TSI 150 KM ACT: równa praca przy odłączaniu cylindrów (stała prędkość, lekki gaz)")
    elif eng == "1.0":
        out += ["• lekkie drgania 3 cylindrów są normalne – niepokojące są stuki i nierówna praca",
                "• wycieki oleju, praca turbo (świst, dym)"]
    else:
        out.append("• rozrząd: kiedy wymieniany, wycieki płynów, równa praca na zimnym")

    out.append("\n<b>Skrzynia</b>")
    if wet_dsg:
        out += ["• DSG mokra (6-biegowa): wymiana oleju co ok. 60 tys. km – faktura",
                "• płynne zmiany biegów, brak szarpnięć przy redukcji"]
    elif dsg:
        out += ["• DSG 7 sucha (DQ200): szarpanie przy ruszaniu i manewrach, stuki przy zmianie biegów",
                "• zapytaj o naprawy mechatroniki / sprzęgieł, sprawdź błędy skrzyni w diagnostyce"]
    else:
        out += ["• sprzęgło: ruszanie pod górę, czy nie ślizga się na 4.–5. biegu przy mocnym gazie",
                "• zmiany biegów bez zgrzytów (szczególnie 2. i 3.)"]

    extra = []
    if l.trim == "r-line" or l.rline != "nie":
        extra.append("• prawdziwy R-Line: kierownica ze spłaszczonym dołem i logo R-Line, zderzaki i progi R-Line, "
                     "sportowe fotele z logo, logo R-Line na błotnikach")
    if l.facelift:
        extra.append("• 7.5: multimedia (Composition/Discover), wirtualny kokpit jeśli jest – czy wszystko działa")
    if extra:
        out.append("\n<b>Wersja</b>")
        out += extra
    out.append("\n• nie wpłacaj zaliczki za auto, którego nie widziałeś")
    return "\n".join(out)
