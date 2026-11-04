"""Wspólne rzeczy dla scraperów: klient HTTP z limitem tempa i wyciąganie JSON-a ze stron."""
from __future__ import annotations

import json
import logging
import random
import re
import time

import httpx

log = logging.getLogger("sniper.http")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")


class ScraperError(Exception):
    pass


try:  # łączy się jak prawdziwy Chrome – portale blokują "odcisk" zwykłych bibliotek Pythona
    from curl_cffi import requests as chrome_requests
except ImportError:  # pragma: no cover
    chrome_requests = None


class _ChromeClient:
    """Ten sam interfejs co httpx.Client.get, ale przez curl_cffi z odciskiem Chrome."""

    def __init__(self):
        self.session = chrome_requests.Session(impersonate="chrome")
        self.session.headers.update({"Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8"})

    def get(self, url, params=None):
        try:
            return self.session.get(url, params=params, timeout=25, allow_redirects=True)
        except Exception as e:  # noqa: BLE001 – ujednolicamy błędy sieci
            raise httpx.TransportError(str(e)) from e


class Http:
    """Jeden klient na portal, min. odstęp między zapytaniami + losowy jitter."""

    def __init__(self, min_delay: float = 2.5, max_delay: float = 6.0):
        if chrome_requests is not None:
            self.client = _ChromeClient()
        else:
            log.warning("Brak curl_cffi – portale mogą zwracać 403. Zainstaluj: pip install curl_cffi")
            self.client = httpx.Client(
                headers={"User-Agent": USER_AGENT, "Accept-Language": "pl-PL,pl;q=0.9",
                         "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"},
                timeout=25, follow_redirects=True, http2=False)
        self.min_delay, self.max_delay = min_delay, max_delay
        self._last = 0.0

    def get(self, url: str, params: dict | None = None, *, as_json: bool = False):
        wait = self._last + random.uniform(self.min_delay, self.max_delay) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            self._last = time.monotonic()
            try:
                r = self.client.get(url, params=params)
            except httpx.HTTPError as e:
                log.warning("Błąd sieci %s (%s), próba %d", url, e, attempt + 1)
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code in (429, 503):
                log.warning("%s zwrócił %d – zwalniam", url, r.status_code)
                time.sleep(60 * (attempt + 1))
                continue
            if r.status_code == 404:
                return None
            if r.status_code == 403:
                raise ScraperError(f"HTTP 403 dla {url} – portal blokuje to połączenie")
            if r.status_code >= 400:
                raise ScraperError(f"HTTP {r.status_code} dla {url}")
            return r.json() if as_json else r.text
        raise ScraperError(f"Nie udało się pobrać {url}")


NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def next_data(html: str) -> dict:
    m = NEXT_DATA_RE.search(html or "")
    if not m:
        raise ScraperError("Brak __NEXT_DATA__ – portal zmienił stronę albo zablokował zapytanie")
    return json.loads(m.group(1))


def walk(obj):
    """Przechodzi rekurencyjnie po JSON-ie (także po stringach zawierających JSON)."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, str) and len(cur) > 50 and cur[:1] in "{[":
            try:
                stack.append(json.loads(cur))
            except ValueError:
                pass
