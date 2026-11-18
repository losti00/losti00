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

## Clips aus schon gelaufenen Streams

```bash
python -m clipper vod
```

Holt ueber die offizielle Twitch-API die meistgesehenen Zuschauer-Clips der letzten 24 Stunden (nur Deutsch
und Englisch), laedt die jeweilige Stelle mit etwas Vorlauf und Nachlauf aus der Aufzeichnung und macht
daraus fertige Hochformat-Clips mit mindestens 61 Sekunden. Es wird immer nur der Ausschnitt geladen, nicht
der ganze Stream - das klappt auch mit langsamer Leitung. Bereits verarbeitete Clips werden uebersprungen.

## Mehr Clips

- mehr `[[channel]]`-Bloecke (jeder Stream braucht ca. 6–8 Mbit/s bei `best`, sonst `quality = "720p60,best"`)
- `min_score` senken (5), `factor` senken (2.5), `cooldown` senken (25)
- kleine Chats: `min_messages = 4`
- `workers` erhoehen, wenn die CPU beim Rendern mitkommt

## Facecam

Die Facecam wird pro Clip automatisch per Gesichtserkennung gefunden (ein Gesicht, das ueber den ganzen
Clip an derselben Stelle bleibt) und nach oben gesetzt, das Gameplay kommt darunter. Wird keine gefunden,
erscheint das ganze Bild mittig auf unscharfem Hintergrund. Manuell ueberschreiben:
`cam = [x, y, breite, hoehe]` als Anteil des Bildes im jeweiligen `[[channel]]`-Block.

## Clip-Laenge, Titel, Untertitel

- Jeder Clip ist mindestens `min_clip_seconds` (61) lang. Anfang und Ende werden auf Sprechpausen gelegt,
  damit kein Satz abgeschnitten wird.
- Neben jedem Clip liegt eine `.txt` mit dem Titel. Mit Ollama schreibt die KI einen Titel in der Sprache
  des Streamers; ohne Ollama wird der Satz vor dem Chat-Spike genommen.
- Untertitel werden Wort fuer Wort eingebrannt (Whisper erkennt Deutsch/Englisch automatisch).
