"""Bot na Telegramie: alerty z przyciskami ⭐/🙈 i kilka prostych komend."""
from __future__ import annotations

import html
import logging
import threading
import time

import httpx

from .config import Settings
from .db import DB
from .models import Listing
from .scoring import Result

log = logging.getLogger("sniper.telegram")

TIER_HEAD = {"perelka": "💎 PEREŁKA", "okazja": "🔥 Okazja", "raport": "👀 Do obejrzenia"}

SOURCES = {"otomoto": "OtoMoto", "olx": "OLX", "autoplac": "Autoplac", "sprzedajemy": "Sprzedajemy",
           "autoscout24": "AutoScout24 (import)"}

COMMANDS = """/start – ta wiadomość
/status – czy bot działa, ile ofert w bazie
/raport – najlepsze oferty z ostatnich 24 h
/ulubione – oferty oznaczone ⭐
/pauza i /wznow – wstrzymanie alertów (💎 perełki przychodzą zawsze)"""


def start_text(s: Settings) -> str:
    return f"""🚗 <b>Golf 7 Sniper</b> – szukam dla Ciebie Golfa

<b>Czego szukam</b>
• Golf 7 (2013–2016) i Golf 7.5 (2017–2020)
• tylko 5-drzwiowy hatchback
• do {fmt_pln(s.budget)} (do {fmt_pln(s.budget + s.budget_tolerance)} przy „do negocjacji”)
• przebieg do {fmt_km(s.max_mileage)} (7.5: do {fmt_km(s.max_mileage + 20000)})
• benzyna i diesel, manual i DSG

<b>Wersje</b>
⭐ R-Line – najwyższy priorytet
🏁 GTI, GTD
👍 Highline, Join, Sound, Lounge, IQ.Drive
✔️ Comfortline, Allstar, Trendline, BlueMotion
✖️ pomijam: Variant, Sportsvan, Alltrack, 3-drzwiowe, e-Golf, GTE
⚠️ Golf R (300 KM) za te pieniądze = podejrzana oferta

<b>Gdzie szukam</b>
{", ".join(SOURCES.values())}

<b>Alerty</b>
💎 perełka (80+ pkt) – dzwoń od razu
🔥 okazja (65+ pkt) – warto obejrzeć
📋 raport dzienny o {s.daily_report_hour}:00

<b>Komendy</b>
{COMMANDS}"""


def fmt_pln(v: int | None) -> str:
    return "?" if v is None else f"{v:,}".replace(",", " ") + " zł"


def fmt_km(v: int | None) -> str:
    return "? km" if v is None else f"{v:,}".replace(",", " ") + " km"


def format_alert(l: Listing, r: Result, *, price_drop: int | None = None) -> str:
    e = html.escape
    head = TIER_HEAD.get(r.tier or "raport", "👀")
    version = "7.5" if l.facelift else "7"
    trim = {"r-line": "R-Line", "golf-r": "R"}.get(l.trim or "", (l.trim or "").title())
    engine = ""
    if l.engine_cc:
        engine = f"{l.engine_cc / 1000:.1f} {'TDI' if l.fuel == 'diesel' else 'TSI'}"
    parts = [f"Golf {version}", trim, engine,
             "DSG" if l.gearbox == "automat" else ""]
    name = " ".join(p for p in parts if p)
    lines = [
        f"<b>{head} · {r.score}/100</b>",
        f"<b>{e(name)}</b> · {l.year or '?'} · {fmt_km(l.mileage_km)}",
        f"<i>{e(l.title[:90])}</i>",
        "",
    ]
    price_line = f"💰 <b>{fmt_pln(l.price_pln)}</b>"
    if l.extra.get("import") and l.extra.get("price_eur"):
        eur = f"{int(l.extra['price_eur']):,}".replace(",", " ")
        price_line += f" (auto {eur} € + szac. koszty sprowadzenia)"
    if l.negotiable:
        price_line += " (do negocjacji)"
    if r.expected:
        sign = "−" if (r.diff_pct or 0) >= 0 else "+"
        price_line += f"\n📊 rynek ~{fmt_pln(r.expected)} ({sign}{abs(r.diff_pct or 0):.0%})"
        price_line += f" · pewność wyceny: {r.confidence}"
        if r.similar:
            price_line += f", {r.similar} podobnych"
    lines.append(price_line)
    if price_drop:
        lines.append(f"📉 obniżka o {fmt_pln(price_drop)}")
    meta = [x for x in (l.fuel, f"{l.power_hp} KM" if l.power_hp else None,
                        l.seller_type, l.location) if x]
    lines.append("ℹ️ " + e(" · ".join(meta)))
    if r.plus:
        lines.append("✅ " + e(" · ".join(r.plus)))
    for f in r.flags:
        lines.append("⚠️ " + e(f))
    if l.vin:
        lines.append(f"🔎 VIN: <code>{e(l.vin)}</code>")
    lines.append(f"\n<a href=\"{e(l.url)}\">Otwórz ogłoszenie – {SOURCES.get(l.source, l.source)}</a>")
    return "\n".join(lines)


def alert_keyboard(l: Listing) -> dict:
    rows = [[{"text": "⭐ Interesuje mnie", "callback_data": f"fav|{l.key}"[:64]},
             {"text": "🙈 Ignoruj", "callback_data": f"ign|{l.key}"[:64]}]]
    if l.vin:
        rows.append([{"text": "Historia pojazdu (gov.pl)",
                      "url": "https://historiapojazdu.gov.pl/"}])
    return {"inline_keyboard": rows}


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
        payload = {"chat_id": self.s.telegram_chat_id, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": False, "disable_notification": silent}
        if keyboard:
            payload["reply_markup"] = keyboard
        return self._call("sendMessage", **payload)

    def send_alert(self, l: Listing, r: Result, price_drop: int | None = None):
        if self.paused and r.tier != "perelka":
            return False
        self.send(format_alert(l, r, price_drop=price_drop), alert_keyboard(l),
                  silent=(r.tier == "raport"))
        return True

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
            return start_text(self.s)
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
        gone = "" if r["active"] else " · nieaktywne"
        return (f"• {r['score']}/100 · {r['year']} · {fmt_km(r['mileage'])} · {fmt_pln(r['price'])}"
                f"{gone} – <a href=\"{html.escape(r['url'])}\">link</a>")

    def report_text(self) -> str:
        rows = self.db.recent_candidates(24)
        if not rows:
            return "📋 Ostatnie 24 h: nic nowego, co pasuje do kryteriów."
        return "📋 <b>Najlepsze z ostatnich 24 h</b>\n" + "\n".join(self._row_line(r) for r in rows)

    def load_saved_settings(self):
        """Ustawienia są w .env – komendy do ich zmiany zostały usunięte."""
        return None
