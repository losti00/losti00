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


def _auth(auto):
    cid = os.environ.get("TWITCH_CLIENT_ID") or auto.get("client_id", "")
    secret = os.environ.get("TWITCH_CLIENT_SECRET") or auto.get("client_secret", "")
    if not cid or not secret:
        raise SystemExit("[auto] braucht client_id und client_secret (kostenlos: dev.twitch.tv/console/apps).")
    tok = _get("https://id.twitch.tv/oauth2/token", data=urllib.parse.urlencode(
        {"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"}).encode())["access_token"]
    return {"Client-Id": cid, "Authorization": f"Bearer {tok}"}


def _langs(auto):
    langs = auto.get("languages") or auto.get("language") or []
    return [langs] if isinstance(langs, str) else list(langs)


def viewer_clips(auto, vod, channels=()):
    """Von Zuschauern erstellte Clips der letzten Stunden (meistgesehen zuerst), die sich einer
    Stelle in einer Aufzeichnung (VOD) zuordnen lassen. Quelle: offizielle Twitch-API."""
    import datetime
    h = _auth(auto)
    now = datetime.datetime.now(datetime.timezone.utc)
    span = [("started_at", (now - datetime.timedelta(hours=vod["hours"])).strftime("%Y-%m-%dT%H:%M:%SZ")),
            ("ended_at", now.strftime("%Y-%m-%dT%H:%M:%SZ")), ("first", "100")]
    sources = []
    if auto.get("games"):
        games = _get("https://api.twitch.tv/helix/games?" + urllib.parse.urlencode(
            [("name", g) for g in auto["games"]]), h)["data"]
        sources += [("game_id", g["id"]) for g in games]
    if channels:
        users = _get("https://api.twitch.tv/helix/users?" + urllib.parse.urlencode(
            [("login", c) for c in channels]), h)["data"]
        sources += [("broadcaster_id", u["id"]) for u in users]
    langs, found = _langs(auto), {}
    for key, value in sources:
        cursor = None
        for _ in range(vod.get("pages", 5)):
            params = [(key, value)] + span + ([("after", cursor)] if cursor else [])
            page = _get("https://api.twitch.tv/helix/clips?" + urllib.parse.urlencode(params), h)
            for c in page.get("data", []):
                by_channel = key == "broadcaster_id"
                if langs and not by_channel and not any((c.get("language") or "").startswith(l) for l in langs):
                    continue
                found[c["id"]] = c
            cursor = (page.get("pagination") or {}).get("cursor")
            if not cursor:
                break
    clips = sorted(found.values(), key=lambda c: -c.get("view_count", 0))
    usable = [c for c in clips if c.get("video_id") and c.get("vod_offset") is not None
              and c.get("view_count", 0) >= vod.get("min_views", 0)]
    log.info("%d Zuschauer-Clips gefunden, %d davon mit Aufzeichnung nutzbar", len(clips), len(usable))
    return usable


def top_channels(auto):
    """auto = [auto]-Abschnitt der Config. Liefert Kanalnamen, groesste zuerst."""
    h = _auth(auto)
    games = _get("https://api.twitch.tv/helix/games?" + urllib.parse.urlencode(
        [("name", g) for g in auto["games"]]), h)["data"]
    if not games:
        log.warning("Kein Spiel gefunden fuer %s", auto["games"])
        return []
    params = [("game_id", g["id"]) for g in games] + [("first", "100"), ("type", "live")]
    params += [("language", l) for l in _langs(auto)]
    streams = _get("https://api.twitch.tv/helix/streams?" + urllib.parse.urlencode(params), h)["data"]
    streams.sort(key=lambda s: -s["viewer_count"])
    top = streams[:auto.get("top", 10)]
    for s in top:
        log.info("Top-Stream: %-25s %6d Zuschauer  %s", s["user_login"], s["viewer_count"], s["game_name"])
    return [s["user_login"].lower() for s in top]
