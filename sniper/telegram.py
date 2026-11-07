"""Bot na Telegramie: alerty z przyciskami ⭐/🙈 i kilka prostych komend."""
from __future__ import annotations

import html
import json
import re
import logging
import threading
import time

import httpx

from .config import Settings
from .db import DB
from .models import Listing
from .checklist import checklist
from .scoring import Result
from .scrapers.base import chrome_requests

log = logging.getLogger("sniper.telegram")

TIER_HEAD = {"perelka": "💎 PEREŁKA", "okazja": "🔥 Okazja", "raport": "👀 Do obejrzenia"}

SOURCES = {"otomoto": "OtoMoto", "olx": "OLX", "autoplac": "Autoplac", "sprzedajemy": "Sprzedajemy",
           "autoscout24": "AutoScout24 (import)"}

COMMANDS = """/start – ta wiadomość
/status – czy bot działa, ile ofert w bazie
/raport – wszystkie aktualne oferty, które pasują (od najlepszej)
/ulubione – oferty oznaczone ⭐
/pauza i /wznow – wstrzymanie alertów (💎 perełki przychodzą zawsze)"""


def start_text(s: Settings, sources: list[str] | None = None) -> str:
    names = [SOURCES.get(x, x) for x in (sources or [k for k in SOURCES if k != "autoplac"])]
    gear_line = {"manual": "skrzynia manualna (DSG tylko gdy to 💎 perełka)",
                 "automat": "skrzynia automatyczna/DSG (manual tylko gdy to 💎 perełka)"}.get(
        getattr(s, "gearbox_pref", ""), "manual i DSG")
    dist_line = ""
    if getattr(s, "home_city", ""):
        dist_line = f"\n• odległość liczona od: {s.home_city}"
        if getattr(s, "max_distance_km", 0):
            dist_line += f" (do {s.max_distance_km} km, dalej tylko 💎)"
    return f"""🚗 <b>Golf 7 Sniper</b> – szukam dla Ciebie Golfa

<b>Czego szukam</b>
• Golf 7 (2013–2016) i Golf 7.5 (2017–2020)
• tylko 5-drzwiowy hatchback
• do {fmt_pln(s.budget)} (do {fmt_pln(s.budget + s.budget_tolerance)} przy „do negocjacji”)
• przebieg do {fmt_km(s.max_mileage)} (7.5: do {fmt_km(s.max_mileage + 20000)})
• benzyna i diesel
• {gear_line}{dist_line}

<b>Wersje</b>
⭐ R-Line – najwyższy priorytet
🏁 GTI, GTD
👍 Highline, Join, Sound, Lounge, IQ.Drive
✔️ Comfortline, Allstar, Trendline, BlueMotion
✖️ pomijam: Variant, Sportsvan, Alltrack, 3-drzwiowe, e-Golf, GTE
⚠️ Golf R (300 KM) za te pieniądze = podejrzana oferta

<b>Gdzie szukam</b>
{", ".join(names)}

<b>Alerty</b>
💎 perełka (80+ pkt) – dzwoń od razu
🔥 okazja (65+ pkt) – warto obejrzeć
Każdy alert ma zdjęcie i przycisk 📋 „Co sprawdzić przy oględzinach”.
Bot ostrzega przed handlarzami udającymi osoby prywatne i autami wystawianymi ponownie.
📋 raport dzienny o {s.daily_report_hour}:00

<b>Komendy</b>
{COMMANDS}"""


def fmt_pln(v: int | None) -> str:
    return "?" if v is None else f"{v:,}".replace(",", " ") + " zł"


def fmt_km(v: int | None) -> str:
    return "? km" if v is None else f"{v:,}".replace(",", " ") + " km"


def _plain_len(text: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


def format_alert(l: Listing, r: Result, *, price_drop: int | None = None, limit: int = 1000) -> str:
    """Treść alertu. Mieści się w podpisie zdjęcia (Telegram: max 1024 znaki)."""
    e = html.escape
    head = TIER_HEAD.get(r.tier or "raport", "👀")
    version = "7.5" if l.facelift else "7"
    trim = {"r-line": "R-Line", "golf-r": "R"}.get(l.trim or "", (l.trim or "").title())
    engine = ""
    if l.engine_cc:
        engine = f"{l.engine_cc / 1000:.1f} {'TDI' if l.fuel == 'diesel' else 'TSI'}"
    gear = "DSG" if l.gearbox == "automat" else ("manual" if l.gearbox == "manual" else "")
    name = " ".join(p for p in (f"Golf {version}", trim, engine, gear) if p)
    x = l.extra

    top = [f"<b>{head} · {r.score}/100</b>",
           f"<b>{e(name)}</b> · {l.year or '?'} · {fmt_km(l.mileage_km)}"]
    price = f"💰 <b>{fmt_pln(l.price_pln)}</b>"
    if x.get("import") and x.get("price_eur"):
        price += f" (auto {int(x['price_eur']):,} € + sprowadzenie)".replace(",", " ")
    if l.negotiable:
        price += " · do negocjacji"
    top.append(price)
    if r.expected:
        sign = "−" if (r.diff_pct or 0) >= 0 else "+"
        top.append(f"📊 rynek ~{fmt_pln(r.expected)} ({sign}{abs(r.diff_pct or 0):.0%}) · wycena {r.confidence}")
    if price_drop:
        top.append(f"📉 obniżka o {fmt_pln(price_drop)}")

    where = l.location or "?"
    if x.get("distance_km") is not None:
        where += f" · ~{x['distance_km']} km od Ciebie"
    top.append("📍 " + e(where))
    seller = {"prywatny": "prywatny", "firma": "firma", "handlarz": "handlarz ⚠️"}.get(l.seller_type or "", "?")
    meta = [x2 for x2 in (l.fuel, f"{l.power_hp} KM" if l.power_hp else None, seller) if x2]
    top.append("ℹ️ " + e(" · ".join(meta)))

    when = []
    if x.get("listed_days") is not None:
        d = x["listed_days"]
        when.append("wystawione dziś" if d < 1 else f"wisi {d:.0f} dni")
    if x.get("sold_days_median") is not None:
        when.append(f"podobne znikały po ~{x['sold_days_median']:.0f} dniach")
    if when:
        top.append("📅 " + " · ".join(when))
    prev = x.get("previous")
    if prev:
        ago = f"{prev['days_ago']:.0f} dni temu" if prev.get("days_ago") else "wcześniej"
        what = "też wystawione" if prev.get("active") else "♻️ wystawiane już"
        top.append(f"{what} na {e(prev['source'])} {ago} za {fmt_pln(prev.get('price'))}")

    flags = ["⚠️ " + e(f) for f in r.flags]
    plus = ["✅ " + e(" · ".join(r.plus))] if r.plus else []
    vin = [f"🔎 VIN: <code>{e(l.vin)}</code>"] if l.vin else []
    link = [f"<a href=\"{e(l.url)}\">Otwórz ogłoszenie – {SOURCES.get(l.source, l.source)}</a>"]

    # skracamy od najmniej ważnych, aż zmieści się w podpisie zdjęcia
    while True:
        text = "\n".join(top + plus + flags + vin + [""] + link)
        if _plain_len(text) <= limit:
            return text
        if plus:
            plus = []
        elif len(flags) > 2:
            flags = flags[:2]
        elif vin:
            vin = []
        else:
            return text[:limit]


def alert_keyboard(l: Listing) -> dict:
    rows = [[{"text": "⭐ Interesuje mnie", "callback_data": f"fav|{l.key}"[:64]},
             {"text": "🙈 Ignoruj", "callback_data": f"ign|{l.key}"[:64]}],
            [{"text": "📋 Co sprawdzić przy oględzinach", "callback_data": f"chk|{l.key}"[:64]}]]
    return {"inline_keyboard": rows}


def fetch_photo(url: str | None) -> bytes | None:
    """Zdjęcie do alertu (do 8 MB). Bez zdjęcia alert idzie jako zwykły tekst."""
    if not url:
        return None
    try:
        if chrome_requests is not None:
            r = chrome_requests.get(url, impersonate="chrome", timeout=15)
        else:
            r = httpx.get(url, timeout=15, follow_redirects=True)
        if r.status_code == 200 and 1000 < len(r.content) < 8_000_000:
            return r.content
    except Exception as e:  # noqa: BLE001
        log.debug("Zdjęcie %s: %s", url, e)
    return None


class Telegram:
    def __init__(self, s: Settings, db: DB, dry_run: bool = False):
        self.s, self.db, self.dry_run = s, db, dry_run
        self.api = f"https://api.telegram.org/bot{s.telegram_token}"
        self.client = httpx.Client(timeout=40)
        self.paused = bool(db.kv_get("paused", False))
        self.status_fn = None   # ustawia main.py

    # --- wysyłanie ----------------------------------------------------
    def _call(self, method: str, **payload):
        if self.dry_run:
            if method == "sendMessage":
                print("\n" + "=" * 60 + "\n" + payload.get("text", "") + "\n" + "=" * 60)
            return {}
        try:
            r = self.client.post(f"{self.api}/{method}", json=payload)
            data = r.json()
        except Exception as e:  # noqa: BLE001  – brak Telegrama nie może zatrzymać skanu
            log.error("Telegram %s: brak połączenia (%s)", method,
                      str(e).replace(self.s.telegram_token, "***") if self.s.telegram_token else e)
            return None
        if not data.get("ok"):
            log.error("Telegram %s: %s", method, data)
            if data.get("error_code") == 401:
                log.error(">>> Zły TELEGRAM_TOKEN – sprawdź sekret w Settings → Secrets → Actions")
            if data.get("error_code") == 400 and "chat not found" in str(data):
                log.error(">>> Zły TELEGRAM_CHAT_ID albo nie napisałeś do bota /start")
        return data.get("result")

    def send(self, text: str, keyboard: dict | None = None, silent: bool = False):
        if len(text) > 3900:                       # Telegram przyjmuje max 4096 znaków
            chunk, parts = "", []
            for line in text.split("\n"):
                if len(chunk) + len(line) + 1 > 3900:
                    parts.append(chunk); chunk = ""
                chunk += line + "\n"
            parts.append(chunk)
            for p in parts[:-1]:
                self.send(p, silent=silent)
            return self.send(parts[-1], keyboard, silent)
        payload = {"chat_id": self.s.telegram_chat_id, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": False, "disable_notification": silent}
        if keyboard:
            payload["reply_markup"] = keyboard
        return self._call("sendMessage", **payload)

    def send_alert(self, l: Listing, r: Result, price_drop: int | None = None):
        if self.paused and r.tier != "perelka":
            return False
        text = format_alert(l, r, price_drop=price_drop)
        kb = alert_keyboard(l)
        silent = r.tier == "raport"
        photo = None if self.dry_run else fetch_photo(l.photo)
        if photo and self._send_photo(photo, text, kb, silent):
            return True
        self.send(text, kb, silent=silent)
        return True

    def _send_photo(self, photo: bytes, caption: str, keyboard: dict, silent: bool) -> bool:
        try:
            r = self.client.post(f"{self.api}/sendPhoto",
                                 data={"chat_id": self.s.telegram_chat_id, "caption": caption,
                                       "parse_mode": "HTML", "disable_notification": str(silent).lower(),
                                       "reply_markup": json.dumps(keyboard)},
                                 files={"photo": ("auto.jpg", photo)})
            ok = r.json().get("ok")
            if not ok:
                log.warning("Zdjęcie nie poszło (%s) – wysyłam sam tekst", r.json().get("description"))
            return bool(ok)
        except Exception as e:  # noqa: BLE001
            log.warning("Zdjęcie nie poszło (%s) – wysyłam sam tekst", e)
            return False

    # --- komendy ------------------------------------------------------
    def start_polling(self):
        if self.dry_run or not self.s.telegram_token:
            return
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def process_updates_once(self, wait: int = 0) -> int:
        """Odbiera komendy i kliknięcia, które przyszły od ostatniego uruchomienia."""
        if self.dry_run or not self.s.telegram_token:
            return 0
        offset = self.db.kv_get("tg_offset", 0)
        r = self.client.get(f"{self.api}/getUpdates",
                            params={"offset": offset, "timeout": wait}, timeout=wait + 15)
        n = 0
        for upd in r.json().get("result", []):
            self.db.kv_set("tg_offset", upd["update_id"] + 1)
            try:
                self._handle(upd)
            except Exception as e:  # noqa: BLE001
                log.warning("Nie obsłużono wiadomości: %s", e)
            n += 1
        return n

    def _poll_loop(self):
        while True:
            try:
                self.process_updates_once(wait=30)
            except Exception as e:  # noqa: BLE001
                log.warning("Błąd odbierania z Telegrama: %s", e)
                time.sleep(10)

    def _handle(self, upd: dict):
        if "callback_query" in upd:
            cq = upd["callback_query"]
            if str(cq["message"]["chat"]["id"]) != str(self.s.telegram_chat_id):
                return
            action, key = cq["data"].split("|", 1)
            if action == "chk":
                l = self.db.listing(key)
                self._call("answerCallbackQuery", callback_query_id=cq["id"])
                if l:
                    head = f"📋 <b>Co sprawdzić – {html.escape(l.title[:60])}</b>\n\n"
                    self.send(head + checklist(l))
                return
            self.db.set_user_status(key, "fav" if action == "fav" else "ignore")
            self._call("answerCallbackQuery", callback_query_id=cq["id"],
                       text="Dodano do ulubionych ⭐" if action == "fav" else "Nie pokażę więcej")
            return
        msg = upd.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(self.s.telegram_chat_id):
            return   # bot odpowiada tylko Tobie
        text = (msg.get("text") or "").strip()
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        self.send(self.command(cmd, arg.strip()))

    def command(self, cmd: str, arg: str) -> str:
        if cmd in ("/start", "/help", "/pomoc", "/komendy"):
            return start_text(self.s, getattr(self, "sources", None))
        if cmd == "/pauza":
            self.paused = True; self.db.kv_set("paused", True)
            return "⏸ Alerty wstrzymane. 💎 Perełki dalej przychodzą. /wznow żeby włączyć."
        if cmd == "/wznow":
            self.paused = False; self.db.kv_set("paused", False)
            return "▶️ Alerty włączone."
        if cmd == "/status":
            st = self.db.stats()
            extra = self.status_fn() if self.status_fn else ""
            return (f"✅ Bot działa\nW bazie: {st['all']} ogłoszeń ({st['active']} aktywnych)\n"
                    f"Pasujących do kryteriów: {st['candidates']}\nWysłanych alertów: {st['alerts']}\n"
                    f"Alerty: {'wstrzymane ⏸' if self.paused else 'włączone'}\n{extra}")
        if cmd == "/raport":
            return self.report_text()
        if cmd == "/ulubione":
            rows = self.db.favourites()
            if not rows:
                return "Brak ulubionych. Kliknij ⭐ pod alertem."
            return "⭐ <b>Ulubione</b>\n" + "\n".join(self._row_line(r) for r in rows[:20])
        return "Nie znam tej komendy.\n\n" + COMMANDS

    @staticmethod
    def _row_line(r) -> str:
        try:
            d = json.loads(r["data"])
        except (TypeError, ValueError):
            d = {}
        trim = {"r-line": "R-Line", "golf-r": "R"}.get(d.get("trim") or "", (d.get("trim") or "").title())
        name = " ".join(x for x in ("Golf 7.5" if d.get("facelift") else "Golf 7", trim,
                                     "DSG" if d.get("gearbox") == "automat" else "") if x)
        icon = {"perelka": "💎", "okazja": "🔥"}.get(r["tier"], "•")
        gone = "" if r["active"] else " · nieaktywne"
        src = SOURCES.get(r["source"], r["source"]).split(" ")[0]
        return (f"{icon} <b>{r['score']}</b> · {html.escape(name)} · {r['year'] or '?'} · {fmt_km(r['mileage'])} · "
                f"<b>{fmt_pln(r['price'])}</b>{gone} – <a href=\"{html.escape(r['url'])}\">{src}</a>")

    def report_text(self, daily: bool = False) -> str:
        if daily:
            rows = self.db.recent_candidates(24)
            if not rows:
                return "📋 Ostatnie 24 h: nic nowego, co pasuje do kryteriów."
            return "📋 <b>Najlepsze nowe oferty z ostatnich 24 h</b>\n\n" + "\n".join(self._row_line(r) for r in rows)
        rows = self.db.active_candidates(40)
        if not rows:
            return "📋 Na razie żadna aktualna oferta nie spełnia kryteriów."
        return (f"📋 <b>Wszystkie aktualne oferty, które pasują ({len(rows)})</b>\n"
                "💎 perełka · 🔥 okazja · • pozostałe, liczba = punkty\n\n"
                + "\n".join(self._row_line(r) for r in rows))

    def load_saved_settings(self):
        """Ustawienia są w .env – komendy do ich zmiany zostały usunięte."""
        return None
