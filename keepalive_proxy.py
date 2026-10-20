# Keepalive-Proxy für Meshtastic-WLAN-Nodes (TCP, Standard-Port 4403)
# SPDX-License-Identifier: Unlicense
#
# Gemeinfrei (Public Domain), siehe LICENSE / https://unlicense.org
"""Hält die TCP-Verbindung eines Clients zum Node am Leben.

Hintergrund: Ein Meshtastic-Node trennt einen TCP-Client, von dem er 15 Minuten lang nichts
gehört hat. Clients, die im Leerlauf nichts senden (z. B. Mesh Hessen Client 1.6.4.x), werden
deshalb alle 15 Minuten getrennt und verbinden sich neu. Dieser Proxy sitzt dazwischen:

    Client  ->  127.0.0.1:4405 (Proxy)  ->  Node 192.168.10.41:4403

Er reicht alle Bytes unverändert in beide Richtungen weiter und sendet dem Node zusätzlich
einen Heartbeat (ToRadio.heartbeat), sobald der Client eine Zeit lang nichts gesendet hat.
Der Heartbeat wird nur zwischen zwei Rahmen eingefügt, nie mitten in eine Nachricht.

Im Client dann als Host 127.0.0.1 und als Port 4405 eintragen. Zurück zum direkten Betrieb:
wieder Host = IP des Nodes, Port 4403.

Start:  pythonw keepalive_proxy.py [--listen 127.0.0.1:4405] [--node 192.168.10.41:4403] [--interval 60]
"""
import argparse
import logging
import logging.handlers
import socket
import sys
import threading
import time
from pathlib import Path

VERSION = "1.0.0"
START1, START2 = 0x94, 0xC3
HEARTBEAT = bytes([START1, START2, 0x00, 0x02, 0x3A, 0x00])   # Rahmen mit ToRadio{heartbeat}
MAX_FRAME = 512                                                # größte gültige Nutzlast

FROZEN = getattr(sys, "frozen", False)
HERE = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent
log = logging.getLogger("keepalive_proxy")


class FrameTracker:
    """Verfolgt den Strom Client -> Node: steht er gerade zwischen zwei Rahmen?

    Rahmen: 0x94 0xC3 <Länge hoch> <Länge tief> <Nutzlast>. Davor sendet der Client
    "Weck-Bytes" (mehrfach 0x94), die zwischen den Rahmen liegen.
    """

    def __init__(self):
        self.state = 0          # 0 zwischen Rahmen, 1 nach 0x94, 2/3 Länge, 4 Nutzlast
        self.hi = 0
        self.remaining = 0

    @property
    def at_boundary(self):
        return self.state == 0

    def feed(self, data):
        for b in data:
            if self.state == 0:
                if b == START1:
                    self.state = 1
            elif self.state == 1:
                if b == START2:
                    self.state = 2
                elif b != START1:           # weitere 0x94 sind Weck-Bytes
                    self.state = 0
            elif self.state == 2:
                self.hi = b
                self.state = 3
            elif self.state == 3:
                self.remaining = (self.hi << 8) | b
                self.state = 4 if 0 < self.remaining <= MAX_FRAME else 0
            elif self.state == 4:
                self.remaining -= 1
                if self.remaining == 0:
                    self.state = 0


def hostport(text, default_port):
    host, _, port = text.rpartition(":")
    return (host, int(port)) if host else (text, default_port)


class Session:
    """Eine Verbindung Client <-> Node."""

    def __init__(self, client, node_addr, interval):
        self.client = client
        self.node_addr = node_addr
        self.interval = interval
        self.node = None
        self.stop = threading.Event()
        self.lock = threading.Lock()          # schützt Schreiben zum Node und den Tracker
        self.tracker = FrameTracker()
        self.last_tx = time.time()
        self.heartbeats = 0
        self.bytes_up = self.bytes_down = 0
        self.reason = "?"
        self.started = time.time()

    def close(self, reason=None):
        if reason and self.reason == "?":
            self.reason = reason
        self.stop.set()
        for s in (self.client, self.node):
            if s is not None:
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    s.close()
                except OSError:
                    pass

    def run(self):
        try:
            self.node = socket.create_connection(self.node_addr, timeout=10)
            self.node.settimeout(None)
            for s in (self.client, self.node):
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as e:
            log.warning("Node %s:%s nicht erreichbar: %s", *self.node_addr, e)
            self.close("Node nicht erreichbar")
            return
        log.info("Verbunden: Client -> Node %s:%s (Heartbeat nach %s s Stille)", *self.node_addr, self.interval)
        threads = [threading.Thread(target=self._up, daemon=True),
                   threading.Thread(target=self._down, daemon=True),
                   threading.Thread(target=self._beat, daemon=True)]
        for t in threads:
            t.start()
        self.stop.wait()
        self.close()
        for t in threads:
            t.join(2)
        log.info("Getrennt nach %.0f min (Grund: %s) | Heartbeats %d | Client->Node %d B, Node->Client %d B",
                 (time.time() - self.started) / 60, self.reason, self.heartbeats, self.bytes_up, self.bytes_down)

    def _up(self):                      # Client -> Node
        try:
            while not self.stop.is_set():
                data = self.client.recv(4096)
                if not data:
                    self.reason = "Client hat getrennt"
                    break
                with self.lock:
                    self.tracker.feed(data)
                    self.node.sendall(data)
                    self.last_tx = time.time()
                self.bytes_up += len(data)
        except OSError:
            self.reason = "Client-Fehler" if self.reason == "?" else self.reason
        finally:
            self.stop.set()

    def _down(self):                    # Node -> Client
        try:
            while not self.stop.is_set():
                data = self.node.recv(4096)
                if not data:
                    self.reason = "Node hat getrennt"
                    break
                self.client.sendall(data)
                self.bytes_down += len(data)
        except OSError:
            self.reason = "Node-Fehler" if self.reason == "?" else self.reason
        finally:
            self.stop.set()

    def _beat(self):                    # Heartbeat bei Stille
        while not self.stop.wait(1.0):
            if time.time() - self.last_tx < self.interval:
                continue
            with self.lock:
                if self.tracker.at_boundary:        # nie mitten in einen Rahmen
                    try:
                        self.node.sendall(HEARTBEAT)
                    except OSError:
                        self.reason = "Node-Fehler beim Heartbeat"
                        self.stop.set()
                        return
                    self.heartbeats += 1
                    self.last_tx = time.time()
                    if self.heartbeats == 1 or self.heartbeats % 15 == 0:     # nicht jede Minute loggen
                        log.info("Heartbeat Nr. %d gesendet (Verbindung steht seit %.0f min)",
                                 self.heartbeats, (time.time() - self.started) / 60)


def serve(listen_addr, node_addr, interval):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(listen_addr)
    srv.listen(5)
    log.info("Keepalive-Proxy %s lauscht auf %s:%s -> Node %s:%s", VERSION, *listen_addr, *node_addr)
    current = None
    while True:
        client, addr = srv.accept()
        if current is not None:                      # wie der Node: ein neuer Client ersetzt den alten
            current.close("durch neuen Client ersetzt")
        current = Session(client, node_addr, interval)
        threading.Thread(target=current.run, daemon=True).start()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Keepalive-Proxy für Meshtastic-WLAN-Nodes")
    ap.add_argument("--listen", default="127.0.0.1:4405", help="Adresse für den Client (Standard 127.0.0.1:4405)")
    ap.add_argument("--node", default="192.168.10.41:4403", help="Adresse des Nodes (Standard 192.168.10.41:4403)")
    ap.add_argument("--interval", type=float, default=60, help="Sekunden Stille bis zum Heartbeat (Standard 60)")
    ap.add_argument("--log", default=str(HERE / "keepalive_proxy.log"), help="Logdatei")
    args = ap.parse_args(argv)
    handler = logging.handlers.RotatingFileHandler(args.log, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    try:
        serve(hostport(args.listen, 4405), hostport(args.node, 4403), args.interval)
    except Exception:
        log.exception("Proxy beendet")
        raise


if __name__ == "__main__":
    main()
