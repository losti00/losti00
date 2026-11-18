"""Schritt 5: Fertigen Clip per Telegram-Bot aufs Handy schicken."""
import json
import logging
import os
import urllib.request
import uuid
from pathlib import Path

log = logging.getLogger("telegram")
API = "https://api.telegram.org/bot{}/{}"


def token(cfg):
    return os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram", {}).get("bot_token", "")


def send_video(cfg, path, caption):
    tok, chat = token(cfg), str(cfg.get("telegram", {}).get("chat_id", ""))
    if not tok or not chat:
        return False
    path = Path(path)
    if path.stat().st_size > 49 * 1024 * 1024:
        return send_text(cfg, caption + f"\n(Datei zu gross fuer Telegram: {path})")
    b = uuid.uuid4().hex
    body = b""
    for k, v in {"chat_id": chat, "caption": caption[:1000], "supports_streaming": "true",
                 "width": "1080", "height": "1920"}.items():
        body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += (f'--{b}\r\nContent-Disposition: form-data; name="video"; filename="{path.name}"\r\n'
             "Content-Type: video/mp4\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{b}--\r\n".encode()
    return _post(tok, "sendVideo", body, f"multipart/form-data; boundary={b}")


def send_text(cfg, text):
    tok, chat = token(cfg), str(cfg.get("telegram", {}).get("chat_id", ""))
    if not tok or not chat:
        return False
    return _post(tok, "sendMessage", json.dumps({"chat_id": chat, "text": text}).encode(), "application/json")


def _post(tok, method, body, ctype):
    try:
        req = urllib.request.Request(API.format(tok, method), data=body, headers={"Content-Type": ctype})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        log.warning("Telegram %s fehlgeschlagen: %s", method, e)
        return False


def print_chat_ids(cfg):
    """Hilfe beim Einrichten: zeigt die chat_id aller Chats, die dem Bot geschrieben haben."""
    tok = token(cfg)
    if not tok:
        raise SystemExit("Kein bot_token gesetzt (config.toml oder TELEGRAM_BOT_TOKEN).")
    with urllib.request.urlopen(API.format(tok, "getUpdates"), timeout=30) as r:
        updates = json.loads(r.read()).get("result", [])
    chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name") or u["message"]["chat"].get("title", "")
             for u in updates if "message" in u}
    if not chats:
        print("Noch keine Nachricht gefunden. Schreib deinem Bot in Telegram irgendwas und starte das nochmal.")
    for cid, name in chats.items():
        print(f"chat_id = {cid}   ({name})")
    if cfg.get("telegram", {}).get("chat_id"):
        print("Testnachricht gesendet:", send_text(cfg, "Clip-Pipeline ist verbunden ✅"))
