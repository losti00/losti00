"""Findet automatisch die groessten Live-Streams eines Spiels ueber die offizielle Twitch-API (Helix)."""
import json
import logging
import os
import urllib.parse
import urllib.request

log = logging.getLogger("discover")


def _get(url, headers=None, data=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def top_channels(auto):
    """auto = [auto]-Abschnitt der Config. Liefert Kanalnamen, groesste zuerst."""
    cid = os.environ.get("TWITCH_CLIENT_ID") or auto.get("client_id", "")
    secret = os.environ.get("TWITCH_CLIENT_SECRET") or auto.get("client_secret", "")
    if not cid or not secret:
        raise SystemExit("[auto] braucht client_id und client_secret (kostenlos: dev.twitch.tv/console/apps).")
    tok = _get("https://id.twitch.tv/oauth2/token", data=urllib.parse.urlencode(
        {"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"}).encode())["access_token"]
    h = {"Client-Id": cid, "Authorization": f"Bearer {tok}"}
    games = _get("https://api.twitch.tv/helix/games?" + urllib.parse.urlencode(
        [("name", g) for g in auto["games"]]), h)["data"]
    if not games:
        log.warning("Kein Spiel gefunden fuer %s", auto["games"])
        return []
    params = [("game_id", g["id"]) for g in games] + [("first", "100"), ("type", "live")]
    if auto.get("language"):
        params.append(("language", auto["language"]))
    streams = _get("https://api.twitch.tv/helix/streams?" + urllib.parse.urlencode(params), h)["data"]
    streams.sort(key=lambda s: -s["viewer_count"])
    top = streams[:auto.get("top", 10)]
    for s in top:
        log.info("Top-Stream: %-25s %6d Zuschauer  %s", s["user_login"], s["viewer_count"], s["game_name"])
    return [s["user_login"].lower() for s in top]
