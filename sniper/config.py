"""Ustawienia aplikacji. Wartości z pliku .env / zmiennych środowiskowych."""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


@dataclass
class Settings:
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "").strip()
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    db_path: str = os.getenv("DB_PATH", "sniper.db")

    # Kryteria (można zmieniać komendami w bocie)
    budget: int = int(os.getenv("BUDGET", "40000"))
    budget_tolerance: int = int(os.getenv("BUDGET_TOLERANCE", "4000"))  # "do negocjacji"
    max_mileage: int = int(os.getenv("MAX_MILEAGE", "180000"))
    year_from: int = 2013
    year_to: int = 2020
    only_rline: bool = os.getenv("ONLY_RLINE", "0") == "1"

    # Częstotliwość (sekundy)
    poll_interval: int = int(os.getenv("POLL_INTERVAL", "240"))
    market_interval: int = 6 * 3600
    daily_report_hour: int = 20

    # Progi alertów
    tier_pearl: int = 80
    tier_deal: int = 65
    tier_report: int = 50

    exclude_words: list[str] = field(default_factory=lambda: [
        "uszkodz", "zalan", "powodzi", "anglik", "kierownica po prawej", "na części",
        "na czesci", "bez dokument", "licytac", "cesja", "spalon", "po pożarze",
    ])


settings = Settings()
