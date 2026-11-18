"""Schritt 2: Live-Chat mitlesen und Spikes (Nachrichten/Sekunde) erkennen."""
import logging
import re
import socket
import ssl
import threading
import time
from collections import deque
from dataclasses import dataclass, field

log = logging.getLogger("chat")

HYPE = re.compile(
    r"(clip|kekw|lul|lol|lmao|omegalul|pog|wtf|omg|krank|nein|no way|was\b|waaa|haha|xd|\?\?\?|!!!|"
    r"\bw\b|\bgg\b|insane|crazy|holy|digga|alter|bruh|💀|😂|🤣)", re.I)


@dataclass
class Spike:
    channel: str
    t: float            # Wanduhr-Zeitpunkt der Erkennung
    ratio: float        # Chat-Tempo relativ zum Normalwert
    count: int          # Nachrichten im Fenster
    messages: list = field(default_factory=list)


class Detector:
    def __init__(self, window=10, baseline=120, factor=3.0, min_messages=8, cooldown=40):
        self.window, self.baseline = window, baseline
        self.factor, self.min_messages, self.cooldown = factor, min_messages, cooldown
        self.lock = threading.Lock()
        self.msgs = {}          # channel -> deque[(ts, text)]
        self.first = {}         # channel -> erster Zeitstempel
        self.last_trigger = {}

    def feed(self, channel, text, ts=None):
        ts = ts or time.time()
        with self.lock:
            self.msgs.setdefault(channel, deque()).append((ts, text))
            self.first.setdefault(channel, ts)

    def between(self, channel, a, b):
        with self.lock:
            return [m for t, m in self.msgs.get(channel, ()) if a <= t <= b]

    def tick(self, now=None):
        """Einmal pro Sekunde aufrufen. Liefert neue Spikes."""
        now = now or time.time()
        spikes = []
        with self.lock:
            for ch, q in self.msgs.items():
                while q and q[0][0] < now - self.window - self.baseline - 60:
                    q.popleft()
                # Warm-up: erst nach einer Minute Chat-Historie bewerten
                if now - self.first[ch] < self.window + 60:
                    continue
                if now - self.last_trigger.get(ch, 0) < self.cooldown:
                    continue
                w0 = now - self.window
                recent = [m for t, m in q if t > w0]
                if len(recent) < self.min_messages:
                    continue
                span = min(self.baseline, w0 - self.first[ch])
                base = sum(1 for t, _ in q if w0 - span < t <= w0) / span * self.window
                ratio = len(recent) / max(base, 1.0)
                if ratio >= self.factor:
                    self.last_trigger[ch] = now
                    spikes.append(Spike(ch, now, ratio, len(recent), recent[-40:]))
        return spikes


def hype_fraction(messages):
    if not messages:
        return 0.0
    return sum(1 for m in messages if HYPE.search(m) or (m.isupper() and len(m) > 2)) / len(messages)


class TwitchChat(threading.Thread):
    """Liest anonym (ohne Login) den Chat beliebig vieler Kanaele ueber IRC."""

    def __init__(self, channels, detector):
        super().__init__(daemon=True, name="chat")
        self.channels = [c.lower() for c in channels]
        self.detector = detector
        self.connected = False
        self.sock = None

    def add(self, channel):
        """Neuen Kanal aufnehmen: Verbindung wird neu aufgebaut und tritt allen Kanaelen bei."""
        if channel.lower() not in self.channels:
            self.channels.append(channel.lower())
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass

    def run(self):
        while True:
            try:
                self._loop()
            except Exception as e:
                log.warning("Chat-Verbindung verloren (%s), verbinde neu", e)
            self.connected = False
            time.sleep(5)

    def _loop(self):
        raw = socket.create_connection(("irc.chat.twitch.tv", 6697), timeout=30)
        s = ssl.create_default_context().wrap_socket(raw, server_hostname="irc.chat.twitch.tv")
        self.sock = s
        s.settimeout(360)  # Twitch pingt ca. alle 5 Minuten
        s.sendall(b"NICK justinfan73914\r\n")
        for i in range(0, len(self.channels), 15):  # Join-Limit: 20 pro 10 s
            s.sendall(("JOIN " + ",".join("#" + c for c in self.channels[i:i + 15]) + "\r\n").encode())
            if i + 15 < len(self.channels):
                time.sleep(11)
        self.connected = True
        log.info("Chat verbunden: %s", ", ".join(self.channels))
        buf = b""
        while True:
            data = s.recv(65536)
            if not data:
                raise ConnectionError("geschlossen")
            buf += data
            *lines, buf = buf.split(b"\r\n")
            for line in lines:
                line = line.decode("utf-8", "replace")
                if line.startswith("PING"):
                    s.sendall(b"PONG :tmi.twitch.tv\r\n")
                    continue
                parts = line.split(" ", 3)
                if len(parts) == 4 and parts[1] == "PRIVMSG":
                    self.detector.feed(parts[2].lstrip("#"), parts[3][1:])
