"""Baza SQLite: ogłoszenia, historia cen, ustawienia z bota."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    key TEXT PRIMARY KEY,
    source TEXT, listing_id TEXT, url TEXT,
    data TEXT,
    price INTEGER, year INTEGER, mileage INTEGER,
    vin TEXT,
    passes_filter INTEGER DEFAULT 0,
    first_seen TEXT, last_seen TEXT,
    active INTEGER DEFAULT 1,
    expected_price INTEGER, score INTEGER, tier TEXT,
    alerted_tier TEXT, alerted_price INTEGER,
    user_status TEXT              -- ignore / fav
);
CREATE INDEX IF NOT EXISTS ix_vin ON listings(vin);
CREATE TABLE IF NOT EXISTS price_history (
    key TEXT, ts TEXT, price INTEGER
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""

TIER_RANK = {None: 0, "raport": 1, "okazja": 2, "perelka": 3}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def listed_since(created_at: str | None, first_seen: str | None) -> datetime:
    """Kiedy ogłoszenie się pojawiło: data z portalu, a jak jej nie ma – kiedy bot je pierwszy raz zobaczył."""
    for v in (created_at, first_seen):
        if not v:
            continue
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(listings)")}
            for col, typ in (("removed_at", "TEXT"), ("last_check", "TEXT")):   # dopisane w nowszej wersji
                if col not in cols:
                    self.conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {typ}")
            self.conn.commit()

    # --- ogłoszenia ---------------------------------------------------
    def upsert(self, l: Listing, passes: bool) -> dict:
        """Zapisuje ogłoszenie. Zwraca {'new': bool, 'old_price': int|None}."""
        ts = now()
        with self.lock:
            row = self.conn.execute("SELECT price FROM listings WHERE key=?", (l.key,)).fetchone()
            data = json.dumps(l.to_dict(), ensure_ascii=False)
            if row is None:
                self.conn.execute(
                    "INSERT INTO listings(key,source,listing_id,url,data,price,year,mileage,vin,"
                    "passes_filter,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (l.key, l.source, l.listing_id, l.url, data, l.price_pln, l.year,
                     l.mileage_km, l.vin, int(passes), ts, ts))
                self.conn.execute("INSERT INTO price_history VALUES(?,?,?)", (l.key, ts, l.price_pln))
                self.conn.commit()
                return {"new": True, "old_price": None}
            old = row["price"]
            self.conn.execute(
                "UPDATE listings SET data=?, price=?, year=?, mileage=?, vin=COALESCE(?, vin), "
                "passes_filter=?, last_seen=?, active=1 WHERE key=?",
                (data, l.price_pln, l.year, l.mileage_km, l.vin, int(passes), ts, l.key))
            if l.price_pln is not None and old != l.price_pln:
                self.conn.execute("INSERT INTO price_history VALUES(?,?,?)", (l.key, ts, l.price_pln))
            self.conn.commit()
            return {"new": False, "old_price": old}

    def cleanup_unverified(self, sources: tuple[str, ...]) -> int:
        """Jednorazowo: kandydaci z portali wymagających strony ogłoszenia, których szczegółów
        nie udało się pobrać (stąd przeciekały kombi). Wrócą, jeśli pojawią się w nowym wyszukiwaniu."""
        if self.kv_get("cleanup_2026_11_07"):
            return 0
        n = 0
        with self.lock:
            for r in self.conn.execute("SELECT key, source, data FROM listings WHERE passes_filter=1").fetchall():
                d = json.loads(r["data"])
                body = (d.get("body") or "").lower()
                if (r["source"] in sources and not d.get("detail_loaded")) or "kombi" in body or "combi" in body:
                    self.conn.execute("UPDATE listings SET passes_filter=0 WHERE key=?", (r["key"],))
                    n += 1
            self.conn.commit()
        self.kv_set("cleanup_2026_11_07", True)
        return n

    def save_data(self, l: Listing):
        with self.lock:
            self.conn.execute("UPDATE listings SET data=? WHERE key=?",
                              (json.dumps(l.to_dict(), ensure_ascii=False), l.key))
            self.conn.commit()

    def get(self, key: str) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM listings WHERE key=?", (key,)).fetchone()

    def listing(self, key: str) -> Listing | None:
        row = self.get(key)
        return Listing.from_dict(json.loads(row["data"])) if row else None

    def has_detail(self, key: str) -> bool:
        row = self.get(key)
        return bool(row and json.loads(row["data"]).get("detail_loaded"))

    def set_score(self, key: str, expected: int | None, score: int, tier: str | None):
        with self.lock:
            self.conn.execute("UPDATE listings SET expected_price=?, score=?, tier=? WHERE key=?",
                              (expected, score, tier, key))
            self.conn.commit()

    def mark_alerted(self, key: str, tier: str, price: int | None):
        with self.lock:
            self.conn.execute("UPDATE listings SET alerted_tier=?, alerted_price=? WHERE key=?",
                              (tier, price, key))
            self.conn.commit()

    def set_user_status(self, key: str, status: str | None):
        with self.lock:
            self.conn.execute("UPDATE listings SET user_status=? WHERE key=?", (status, key))
            self.conn.commit()

    def same_car_alerted(self, l: Listing) -> bool:
        """To samo auto z innego portalu (VIN albo rocznik+przebieg+cena) już było zgłoszone."""
        with self.lock:
            if l.vin:
                r = self.conn.execute(
                    "SELECT 1 FROM listings WHERE vin=? AND key<>? AND alerted_tier IS NOT NULL",
                    (l.vin, l.key)).fetchone()
                if r:
                    return True
            if l.year and l.mileage_km and l.price_pln:
                r = self.conn.execute(
                    "SELECT 1 FROM listings WHERE key<>? AND source<>? AND year=? "
                    "AND ABS(mileage-?)<=1000 AND ABS(price-?)<=1500 AND alerted_tier IS NOT NULL",
                    (l.key, l.source, l.year, l.mileage_km, l.price_pln)).fetchone()
                return bool(r)
        return False

    def vin_history(self, l: Listing) -> list[sqlite3.Row]:
        """Inne ogłoszenia z tym samym VIN (np. wcześniej z innym przebiegiem)."""
        if not l.vin:
            return []
        with self.lock:
            return self.conn.execute(
                "SELECT key, price, mileage, first_seen FROM listings WHERE vin=? AND key<>?",
                (l.vin, l.key)).fetchall()

    def mark_inactive(self, older_than_hours: int = 24) -> int:
        """Nie-kandydaci znikający z wyników. Kandydatów (passes_filter=1) sprawdzamy osobno,
        wchodząc na ich stronę – skan widzi tylko pierwsze strony wyników."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
        with self.lock:
            cur = self.conn.execute(
                "UPDATE listings SET active=0 WHERE active=1 AND passes_filter=0 AND last_seen<?", (cutoff,))
            self.conn.commit()
            return cur.rowcount

    # --- czy ogłoszenie dalej wisi / ile wisiało ----------------------
    def due_for_check(self, hours: int = 6, limit: int = 30) -> list[sqlite3.Row]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM listings WHERE passes_filter=1 AND active=1 "
                "AND COALESCE(last_check, first_seen) < ? ORDER BY COALESCE(last_check, first_seen) LIMIT ?",
                (cutoff, limit)).fetchall()

    def mark_checked(self, key: str, alive: bool):
        ts = now()
        with self.lock:
            if alive:
                self.conn.execute("UPDATE listings SET last_check=? WHERE key=?", (ts, key))
            else:
                self.conn.execute("UPDATE listings SET last_check=?, active=0, removed_at=? WHERE key=?",
                                  (ts, ts, key))
            self.conn.commit()

    def sold_days(self, l: Listing) -> list[float]:
        """Ile dni wisiały podobne auta (rocznik ±1, przebieg ±30 tys.), które już zniknęły."""
        if not l.year:
            return []
        with self.lock:
            rows = self.conn.execute(
                "SELECT data, first_seen, removed_at FROM listings WHERE removed_at IS NOT NULL "
                "AND ABS(year-?)<=1 AND (? IS NULL OR ABS(mileage-?)<=30000)",
                (l.year, l.mileage_km, l.mileage_km or 0)).fetchall()
        out = []
        for r in rows:
            start = listed_since(json.loads(r["data"]).get("created_at"), r["first_seen"])
            try:
                end = datetime.fromisoformat(r["removed_at"])
                out.append(max(0.0, (end - start).total_seconds() / 86400))
            except (TypeError, ValueError):
                continue
        return out

    def previous_listing(self, l: Listing) -> sqlite3.Row | None:
        """To samo auto wystawione wcześniej (ten sam VIN albo rocznik + moc + paliwo + skrzynia
        + przebieg ±3 tys. km + to samo miasto)."""
        with self.lock:
            if l.vin:
                r = self.conn.execute(
                    "SELECT * FROM listings WHERE vin=? AND key<>? ORDER BY first_seen LIMIT 1",
                    (l.vin, l.key)).fetchone()
                if r:
                    return r
            if not (l.year and l.mileage_km and l.location):
                return None
            city = l.location.split(",")[0].strip().lower()
            rows = self.conn.execute(
                "SELECT * FROM listings WHERE key<>? AND year=? AND ABS(mileage-?)<=3000 "
                "AND first_seen < datetime('now', '-1 day') ORDER BY first_seen LIMIT 20",
                (l.key, l.year, l.mileage_km)).fetchall()
        for r in rows:
            d = json.loads(r["data"])
            if (d.get("power_hp") == l.power_hp and d.get("fuel") == l.fuel and d.get("gearbox") == l.gearbox
                    and (d.get("location") or "").split(",")[0].strip().lower() == city):
                return r
        return None

    def market_rows(self, days: int = 90) -> list[Listing]:
        """Wszystkie Golfy 7 hatchback z ostatnich dni (także droższe) – do wyceny."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.lock:
            rows = self.conn.execute(
                "SELECT data FROM listings WHERE last_seen>=? AND price IS NOT NULL", (cutoff,)).fetchall()
        out = []
        for r in rows:
            l = Listing.from_dict(json.loads(r["data"]))
            if l.extra.get("golf7_hatch") and not l.extra.get("import"):   # import ma inną cenę
                out.append(l)
        return out

    def recent_candidates(self, hours: int = 24) -> list[sqlite3.Row]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM listings WHERE passes_filter=1 AND first_seen>=? AND score IS NOT NULL "
                "AND (user_status IS NULL OR user_status<>'ignore') ORDER BY score DESC LIMIT 15",
                (cutoff,)).fetchall()

    def active_candidates(self, limit: int = 40) -> list[sqlite3.Row]:
        """Wszystkie aktualne oferty spełniające kryteria, od najlepszej."""
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM listings WHERE passes_filter=1 AND active=1 AND score IS NOT NULL "
                "AND (user_status IS NULL OR user_status<>'ignore') ORDER BY score DESC LIMIT ?",
                (limit,)).fetchall()

    def favourites(self) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM listings WHERE user_status='fav' ORDER BY score DESC").fetchall()

    def stats(self) -> dict:
        with self.lock:
            c = self.conn.execute
            return {
                "all": c("SELECT COUNT(*) FROM listings").fetchone()[0],
                "active": c("SELECT COUNT(*) FROM listings WHERE active=1").fetchone()[0],
                "candidates": c("SELECT COUNT(*) FROM listings WHERE passes_filter=1 AND active=1").fetchone()[0],
                "alerts": c("SELECT COUNT(*) FROM listings WHERE alerted_tier IS NOT NULL").fetchone()[0],
            }

    def price_history(self, key: str) -> list[tuple[str, int]]:
        with self.lock:
            return [(r["ts"], r["price"]) for r in self.conn.execute(
                "SELECT ts, price FROM price_history WHERE key=? ORDER BY ts", (key,))]

    # --- ustawienia ---------------------------------------------------
    def kv_get(self, k: str, default=None):
        with self.lock:
            r = self.conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def kv_set(self, k: str, v):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, json.dumps(v)))
            self.conn.commit()
