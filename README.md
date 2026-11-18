# Clip-Pipeline

Nimmt mehrere Twitch-Streams gleichzeitig auf, erkennt Clip-Momente am Chat, bewertet sie mit
lokaler KI, rendert sie als 9:16 mit Untertiteln und schickt sie per Telegram aufs Handy.

1. **Aufnahme** – `streamlink` + `ffmpeg`, pro Stream die letzten 5 Minuten als lokaler Cache (`buffer/`)
2. **Chat** – liest anonym den Twitch-Chat; wird er ploetzlich x-mal schneller als normal → Clip-Moment
3. **KI-Score** – Whisper-Transkript + Chat → Ollama vergibt 1–10; unter `min_score` wird aussortiert
4. **Format** – Cam oben, Gameplay unten (1080x1920), Untertitel Wort fuer Wort
5. **Handy** – fertiger Clip kommt per Telegram-Bot, du musst nur noch posten

## Start (5 Minuten)

Voraussetzungen: Python 3.11+, `ffmpeg` im PATH.

```bash
pip install -r requirements.txt
cp config.example.toml config.toml      # Kanaele eintragen
python -m clipper selftest              # Offline-Test: erzeugt einen Test-Clip in clips/selftest/
python -m clipper                       # Pipeline starten
```

**Telegram:** bei @BotFather `/newbot` → Token in `config.toml` → dem Bot eine Nachricht schreiben →
`python -m clipper telegram` zeigt die `chat_id` → eintragen.

**Lokale KI (optional, laeuft auch ohne):** [Ollama](https://ollama.com) installieren,
`ollama pull llama3.1:8b`. Ohne Ollama wird der Score aus Chat-Tempo + Hype-Woertern berechnet,
ohne `faster-whisper` gibt es keine Untertitel.

## Top 10 GTA-Streamer automatisch

In `config.toml` unter `[auto]` `enabled = true` setzen und `client_id` / `client_secret` einer eigenen
Twitch-App eintragen (kostenlos: https://dev.twitch.tv/console/apps). Die Pipeline holt dann beim Start
und alle 30 Minuten die zehn groessten Live-Streams der eingetragenen Spiele und clippt sie alle.

## Mehr Clips

- mehr `[[channel]]`-Bloecke (jeder Stream braucht ca. 6–8 Mbit/s bei `best`, sonst `quality = "720p60,best"`)
- `min_score` senken (5), `factor` senken (2.5), `cooldown` senken (25)
- kleine Chats: `min_messages = 4`
- `workers` erhoehen, wenn die CPU beim Rendern mitkommt

## Facecam

`cam = [x, y, breite, hoehe]` als Anteil des Bildes. Beispiel Cam oben rechts, ein Viertel breit:
`[0.75, 0.0, 0.25, 0.25]`. Ohne `cam` wird das ganze Bild mittig auf unscharfem Hintergrund gezeigt.
