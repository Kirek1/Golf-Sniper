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


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
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
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
        with self.lock:
            cur = self.conn.execute("UPDATE listings SET active=0 WHERE active=1 AND last_seen<?", (cutoff,))
            self.conn.commit()
            return cur.rowcount

    def market_rows(self, days: int = 90) -> list[Listing]:
        """Wszystkie Golfy 7 hatchback z ostatnich dni (także droższe) – do wyceny."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.lock:
            rows = self.conn.execute(
                "SELECT data FROM listings WHERE last_seen>=? AND price IS NOT NULL", (cutoff,)).fetchall()
        out = []
        for r in rows:
            l = Listing.from_dict(json.loads(r["data"]))
            if l.extra.get("golf7_hatch"):
                out.append(l)
        return out

    def recent_candidates(self, hours: int = 24) -> list[sqlite3.Row]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM listings WHERE passes_filter=1 AND first_seen>=? AND score IS NOT NULL "
                "AND (user_status IS NULL OR user_status<>'ignore') ORDER BY score DESC LIMIT 15",
                (cutoff,)).fetchall()

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
