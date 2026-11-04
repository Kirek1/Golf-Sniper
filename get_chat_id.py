"""Pomocnik: napisz cokolwiek do swojego bota na Telegramie, potem uruchom
    python get_chat_id.py
a dostaniesz swój TELEGRAM_CHAT_ID do wklejenia w .env."""
import httpx

from sniper.config import settings

if not settings.telegram_token:
    raise SystemExit("Najpierw wpisz TELEGRAM_TOKEN do pliku .env")
r = httpx.get(f"https://api.telegram.org/bot{settings.telegram_token}/getUpdates", timeout=20).json()
chats = {(u.get("message") or {}).get("chat", {}).get("id"): (u.get("message") or {}).get("chat", {})
         for u in r.get("result", []) if u.get("message")}
if not chats:
    print("Brak wiadomości. Wyślij botowi np. /start i uruchom ponownie.")
for cid, chat in chats.items():
    print(f"TELEGRAM_CHAT_ID={cid}   ({chat.get('first_name', '')} {chat.get('username', '')})")
