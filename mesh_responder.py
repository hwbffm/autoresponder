# Mesh-Responder – Tray-Responder für Meshtastic über den Virtual Node des Meshhessen Clients
# SPDX-License-Identifier: Unlicense
#
# Gemeinfrei (Public Domain). Jeder darf dieses Programm ohne Einschränkung nutzen,
# kopieren, verändern, verkaufen und weitergeben – mit oder ohne Namensnennung.
#
# This is free and unencumbered software released into the public domain.
#
# Anyone is free to copy, modify, publish, use, compile, sell, or
# distribute this software, either in source code form or as a compiled
# binary, for any purpose, commercial or non-commercial, and by any
# means.
#
# In jurisdictions that recognize copyright laws, the author or authors
# of this software dedicate any and all copyright interest in the
# software to the public domain. We make this dedication for the benefit
# of the public at large and to the detriment of our heirs and
# successors. We intend this dedication to be an overt act of
# relinquishment in perpetuity of all present and future rights to this
# software under copyright law.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.
#
# For more information, please refer to <https://unlicense.org/>
"""Tray-Responder für ausgewählte Meshtastic-Kanäle und Direktnachrichten.

Verbindet sich mit dem Virtual Node des Meshhessen Clients (Standard 127.0.0.1:4404).
Jede Nachricht in den gewählten Kanälen und jede Direktnachricht (DM) öffnet ein kleines
Fenster; mit "Ja" geht der gewählte (oder ein frei eingegebener) Antworttext dorthin, wo
die Nachricht herkam: Kanalnachricht -> derselbe Kanal, DM -> DM an den Absender.

Ausführliche Beschreibung: Anleitung.txt im selben Ordner.

Start:  pythonw mesh_responder.py          (normal)
        python  mesh_responder.py --demo   (simuliert Nachrichten, sendet nichts)
"""
import csv
import json
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
import winsound
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from tkinter import ttk

import pystray
from PIL import Image, ImageDraw, ImageFont
from pubsub import pub
import meshtastic.tcp_interface as ti
from meshtastic import BROADCAST_NUM as BROADCAST
from meshtastic.protobuf import config_pb2, portnums_pb2

# --- Grundwerte (alles Weitere über das Tray-Menü einstellbar) ----------------
HOST = "127.0.0.1"
PORT = 4404
DEFAULT_CHANNELS = ["ShortSlow", "HessenTest"]   # "ShortSlow" = Primärkanal ohne Namen
POPUP_TIMEOUT_S = 90                            # danach schließt das Fenster ohne Antwort
MAX_BYTES = 200                                 # Meshtastic-Textlimit (ca.)
POPUP_BOTTOM_PX = 320                           # Abstand Fenster–Bildschirmunterkante (Platz für Hinweise
                                                # des Meshhessen Clients und Windows-Benachrichtigungen)
VERSION = "1.1.0"
VERSION_DATE = "Oktober 2026"
# -------------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
SETTINGS_FILE = HERE / "mesh_responder.json"
CSV_FILE = HERE / "mesh_responder_log.csv"
CSV_HEADER = ["Zeit", "Ereignis", "Kanal", "Node-ID", "Name", "Hops", "Text", "SNR", "RSSI", "Weg"]
SLOTS = 9
DEFAULT_TEXT = "In Standort angekommen ✅\n{name} | {short} | {id}\nEmpfangen: {wo} | Hops: {hops}"
PLACEHOLDER_HELP = ("Platzhalter:  {name} Langname   {short} Kurzname   {id} Node-ID   {wo} CHn/DM   "
                    "{kanal} Kanalname   {hops} Hops   {snr} SNR   {rssi} RSSI   "
                    "{text} eingehende Nachricht   {zeit} Uhrzeit")
SAMPLE = {"name": "Nodename", "short": "NODE", "id": "!12345678", "wo": "CH1",
          "kanal": "HessenTest", "hops": 3, "snr": "-7.5", "rssi": "-112", "text": "Test", "zeit": "12:00"}

DEMO = "--demo" in sys.argv
logging.basicConfig(filename=Path(__file__).with_suffix(".log"), encoding="utf-8",
                    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mesh_responder")
events = queue.Queue()        # Aufgaben für den Tk-Thread (Callables)
state = {"iface": None, "all_chans": {}, "chans": {}, "connected": False,
         "status": "Verbinde …", "active": True, "stop": False, "reconnect": False,
         "last_reply": {}}    # Node-ID -> Zeitpunkt der letzten Antwort (Zeitsperre)
wake = threading.Event()      # weckt den Mesh-Thread für sofortigen Neuaufbau
ROOT = None
ICON = None


# --- Emojis ----------------------------------------------------------------------
# Tk 8.6 kürzt unter Windows getippte Emojis (Tastatur, Win+.) um 0x10000:
# 😀 U+1F600 kommt als U+F600 an. Diese Zeichen werden zurückgerechnet.
EMOJI_BROKEN_RE = "[-﫿]"


def repair_emoji(text):
    return "".join(chr(ord(c) + 0x10000) if 0xF000 <= ord(c) <= 0xFAFF else c for c in text)


def fix_emoji_widget(t):
    """Kaputte Emojis in einem tk.Text sofort ersetzen."""
    pos = "1.0"
    while True:
        pos = t.search(EMOJI_BROKEN_RE, pos, "end", regexp=True)
        if not pos:
            return
        t.replace(pos, f"{pos} +1c", repair_emoji(t.get(pos)))
        pos = f"{pos} +1c"


# --- Einstellungen ---------------------------------------------------------------
def default_settings():
    return {"kanal": {"aktiv": 0, "texte": [DEFAULT_TEXT] + [""] * (SLOTS - 1), "aktiv_pro_kanal": {}},
            "dm": {"aktiv": 0, "texte": [DEFAULT_TEXT] + [""] * (SLOTS - 1), "auto": [False] * SLOTS},
            "verbindung": {"host": HOST, "port": PORT},
            "kanaele": list(DEFAULT_CHANNELS),
            "sperrliste": {},     # Node-ID -> Name
            "optionen": {"smileys": False, "mqtt": False, "sperre_min": 10, "ton": True, "benachrichtigung": True,
                         "ruhe_an": False, "ruhe_von": "22:00", "ruhe_bis": "07:00", "ruhe_auto": False}}


def load_settings():
    s = default_settings()
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))   # -sig: auch mit Notepad-BOM
        for key, value in data.items():
            if isinstance(s.get(key), dict) and isinstance(value, dict):
                s[key].update(value)
            elif key in s:
                s[key] = value
        for cat in ("kanal", "dm"):
            s[cat]["texte"] = [repair_emoji(t) for t in s[cat]["texte"]]
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("Einstellungen nicht lesbar – nehme Standardwerte")
    return s


def save_settings():
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


settings = load_settings()


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render(template, info):
    try:
        return template.format_map(SafeDict(info))
    except (ValueError, IndexError):
        return template


def first_line(text, n=40):
    text = text.strip()
    return text.splitlines()[0][:n] if text else "(leer)"


def parse_hhmm(s):
    h, m = s.strip().split(":")
    h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError
    return h * 60 + m


def in_quiet_time(now=None):
    o = settings["optionen"]
    if not o["ruhe_an"]:
        return False
    now = now or datetime.now()
    t = now.hour * 60 + now.minute
    try:
        start, end = parse_hhmm(o["ruhe_von"]), parse_hhmm(o["ruhe_bis"])
    except (ValueError, AttributeError):
        return False                       # unlesbare Zeiten in der JSON-Datei: keine Ruhezeit
    return start <= t < end if start <= end else (t >= start or t < end)   # über Mitternacht


# --- CSV-Log ---------------------------------------------------------------------
csv_lock = threading.Lock()


def migrate_csv():
    """Ältere Logs (ohne SNR/RSSI-Spalten) auf die aktuelle Kopfzeile bringen."""
    if not CSV_FILE.exists():
        return
    with csv_lock:
        try:
            with CSV_FILE.open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f, delimiter=";"))
            if rows and rows[0] == CSV_HEADER:
                return
            with CSV_FILE.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(CSV_HEADER)
                for r in rows[1:]:
                    w.writerow((r + [""] * len(CSV_HEADER))[:len(CSV_HEADER)])
        except OSError as e:
            log.warning("CSV-Log nicht umstellbar: %s", e)


def log_csv(ereignis, info=None, text=None):
    i = info or {}
    row = [time.strftime("%Y-%m-%d %H:%M:%S"), ereignis, i.get("kanal", ""), i.get("id", ""),
           i.get("name", ""), i.get("hops", ""), i.get("text", "") if text is None else text,
           i.get("snr", ""), i.get("rssi", ""), i.get("weg", "")]
    with csv_lock:
        try:
            new = not CSV_FILE.exists()
            # BOM nur beim Anlegen, damit Excel UTF-8 erkennt
            with CSV_FILE.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
                w = csv.writer(f, delimiter=";")
                if new:
                    w.writerow(CSV_HEADER)
                w.writerow(row)
        except OSError as e:
            log.warning("CSV-Log nicht schreibbar (in Excel geöffnet?): %s", e)


def read_csv_rows():
    if not CSV_FILE.exists():
        return []
    with csv_lock, CSV_FILE.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))[1:]
    return [(r + [""] * len(CSV_HEADER))[:len(CSV_HEADER)] for r in rows]


# --- Pakete ----------------------------------------------------------------------
def channel_names(iface):
    """Kanalindex -> Anzeigename; ein leerer Name zeigt das Modem-Preset (z. B. ShortSlow)."""
    preset = config_pb2.Config.LoRaConfig.ModemPreset.Name(iface.localNode.localConfig.lora.modem_preset)
    preset = "".join(p.capitalize() for p in preset.split("_"))
    return {c.index: c.settings.name or preset for c in iface.localNode.channels if c.role}


def apply_channel_filter():
    state["chans"] = {i: n for i, n in state["all_chans"].items() if n in settings["kanaele"]}


def sender_info(pkt, iface):
    frm = pkt.get("fromId") or f"!{pkt['from']:08x}"
    user = (iface.nodes or {}).get(frm, {}).get("user", {}) if iface else {}
    return frm, user.get("longName", frm), user.get("shortName", frm[-4:])


def is_smiley(pkt):
    """Reaktion (Tapback) oder Nachricht, die nur aus Emojis/Symbolen besteht, z. B. "👍" oder "😂😂"."""
    d = pkt.get("decoded", {})
    text = d.get("text", "").strip()
    if d.get("emoji"):
        return True
    return bool(text) and not any(c.isalnum() for c in text) and any(ord(c) >= 0x2000 for c in text)


def is_dm(pkt):
    """Direktnachricht = an unseren Node adressiert statt an alle."""
    return pkt.get("to", BROADCAST) != BROADCAST


def where(pkt):
    return "DM" if is_dm(pkt) else f"CH{pkt.get('channel', 0)}"


def fmt_num(v):
    return "?" if v is None else f"{v:g}"


def pkt_info(pkt, iface):
    frm, long_name, short = sender_info(pkt, iface)
    hops = pkt["hopStart"] - pkt["hopLimit"] if "hopStart" in pkt and "hopLimit" in pkt else "?"
    ch = pkt.get("channel", 0)
    return {"id": frm, "name": long_name, "short": short, "wo": where(pkt), "hops": hops,
            "kanal": "DM" if is_dm(pkt) else state["chans"].get(ch, f"CH{ch}"),
            "snr": fmt_num(pkt.get("rxSnr")), "rssi": fmt_num(pkt.get("rxRssi")),
            "weg": "MQTT" if pkt.get("viaMqtt") else "Funk",
            "text": pkt.get("decoded", {}).get("text", ""), "zeit": time.strftime("%H:%M")}


def on_text(packet, interface):
    if not state["active"] or packet.get("from") == interface.myInfo.my_node_num:
        return
    if not is_dm(packet) and packet.get("channel", 0) not in state["chans"]:
        return
    i = pkt_info(packet, interface)
    if i["id"] in settings["sperrliste"]:
        log_csv("Gesperrt – ignoriert", i)
        return
    if is_smiley(packet) and not settings["optionen"]["smileys"]:
        log_csv("Smiley ignoriert", i)
        return
    if packet.get("viaMqtt") and not settings["optionen"]["mqtt"]:
        log_csv("MQTT ignoriert", i)
        return
    log_csv("Empfangen", i)
    events.put(lambda p=packet: handle_incoming(p))


def on_lost(interface, topic=pub.AUTO_TOPIC):
    if state["connected"]:
        log_csv("Verbindung verloren")
    state["connected"] = False
    state["status"] = "Verbindung verloren"


def mesh_loop():
    pub.subscribe(on_text, "meshtastic.receive.text")
    pub.subscribe(on_lost, "meshtastic.connection.lost")
    while not state["stop"]:
        try:
            state["reconnect"] = False
            host, port = settings["verbindung"]["host"], int(settings["verbindung"]["port"])
            state["status"] = f"Verbinde mit {host}:{port} …"
            iface = ti.TCPInterface(host, portNumber=port)
            state.update(iface=iface, all_chans=channel_names(iface), connected=True)
            apply_channel_filter()
            state["status"] = "Verbunden – " + (", ".join(f"{n} (CH{i})" for i, n in state["chans"].items())
                                                or "kein Kanal gewählt")
            log.info("%s:%s %s", host, port, state["status"])
            log_csv("Verbunden", text=f"{host}:{port} – {state['status']}")
            events.put(rebuild_menu)          # Kanal-Untermenüs aktualisieren
            while not state["stop"] and state["connected"] and not state["reconnect"]:
                wake.wait(2)
                wake.clear()
            state["connected"] = False      # vor close(), damit kein "verloren" geloggt wird
            iface.close()
        except Exception as e:
            state["status"] = f"Nicht verbunden: {e}"
            log.warning("Verbindung: %s", e)
        state.update(iface=None, connected=False)
        if not state["stop"] and not state["reconnect"]:
            wake.wait(10)
            wake.clear()


# --- Antworten (läuft im Tk-Thread) ------------------------------------------------
def active_slot(pkt):
    if is_dm(pkt):
        return settings["dm"]["aktiv"]
    name = state["chans"].get(pkt.get("channel", 0), "")
    return settings["kanal"]["aktiv_pro_kanal"].get(name, settings["kanal"]["aktiv"])


def send_reply(pkt, text, ereignis="Gesendet"):
    iface = state["iface"]
    info = pkt_info(pkt, iface)
    if DEMO or iface is None:
        log_csv(f"Nicht gesendet ({'Demo' if DEMO else 'offline'})", info, text)
        return
    try:
        if is_dm(pkt):
            # DM zurück an den Absender, gleiche Verschlüsselung (PKI) wie die eingehende
            iface.sendData(text.encode("utf-8"), destinationId=pkt["from"],
                           portNum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                           channelIndex=pkt.get("channel", 0), replyId=pkt.get("id"),
                           pkiEncrypted=bool(pkt.get("pkiEncrypted")))
        else:
            iface.sendText(text, channelIndex=pkt.get("channel", 0), replyId=pkt.get("id"))
        state["last_reply"][info["id"]] = time.time()
        log_csv(ereignis, info, text)
    except Exception as e:
        log.exception("Senden fehlgeschlagen")
        log_csv("Fehler beim Senden", info, f"{e} | {text}")


def handle_incoming(pkt):
    info = pkt_info(pkt, state["iface"])
    sperre = settings["optionen"]["sperre_min"]
    last = state["last_reply"].get(info["id"])
    if sperre and last and time.time() - last < sperre * 60:
        log_csv(f"Zeitsperre – schon vor {int((time.time() - last) / 60)} min beantwortet", info)
        return
    cat = "dm" if is_dm(pkt) else "kanal"
    slot = active_slot(pkt)
    text = settings[cat]["texte"][slot]
    if cat == "dm" and settings["dm"]["auto"][slot] and text.strip():
        send_reply(pkt, render(text, info), "Automatisch gesendet")
        return
    if in_quiet_time():
        if settings["optionen"]["ruhe_auto"] and text.strip():
            send_reply(pkt, render(text, info), "Automatisch gesendet (Ruhezeit)")
        else:
            log_csv("Ruhezeit – nicht beantwortet", info)
        return
    Popup(pkt)
    notify(info)


def notify(info):
    o = settings["optionen"]
    if o["ton"]:
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    if o["benachrichtigung"] and ICON is not None:
        try:
            ICON.notify(f"{info['name']}: {info['text']}"[:200], f"Mesh – {info['kanal']}")
        except Exception:
            log.exception("Benachrichtigung fehlgeschlagen")


# --- Fenster -------------------------------------------------------------------------
def single_window(cls):
    """Klassenmethode show(): Fenster nur einmal öffnen, sonst nach vorne holen."""
    @classmethod
    def show(c):
        if c.win is not None and c.win.winfo_exists():
            c.win.deiconify()
            c.win.lift()
            if hasattr(c.inst, "load"):
                c.inst.load()
            return
        c.inst = c()
    cls.win, cls.inst, cls.show = None, None, show
    return cls


class Popup:
    open_count = 0

    def __init__(self, pkt):
        self.pkt = pkt
        self.info = pkt_info(pkt, state["iface"])
        cat = "dm" if is_dm(pkt) else "kanal"
        label = "Direktnachricht" if is_dm(pkt) else f"{self.info['kanal']} ({self.info['wo']})"
        self.slots = [(i, t) for i, t in enumerate(settings[cat]["texte"]) if t.strip()]

        self.win = w = tk.Toplevel(ROOT)
        w.title(f"Mesh – {label}")
        w.attributes("-topmost", True)
        w.resizable(False, False)
        pad = {"padx": 12, "pady": 4, "anchor": "w"}
        tk.Label(w, text=f"{label}  ·  {self.info['zeit']}  ·  Hops {self.info['hops']}  ·  "
                         + ("über MQTT" if self.info["weg"] == "MQTT"
                            else f"SNR {self.info['snr']}  ·  RSSI {self.info['rssi']}"),
                 fg="#888").pack(padx=12, pady=(8, 0), anchor="w")
        tk.Label(w, text=f"{self.info['name']} ({self.info['id']})", font=("Segoe UI", 10, "bold")).pack(**pad)
        tk.Label(w, text=self.info["text"], font=("Segoe UI", 11), wraplength=360, justify="left").pack(**pad)
        tk.Label(w, text="Antwort senden?", font=("Segoe UI", 9, "bold")).pack(padx=12, pady=(10, 0), anchor="w")

        self.combo = ttk.Combobox(w, state="readonly", width=50,
                                  values=[f"{i + 1}: {first_line(t)}" for i, t in self.slots])
        self.combo.pack(**pad)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.update_preview())
        tk.Label(w, text="oder eigener Text (Platzhalter erlaubt):", fg="#666").pack(padx=12, anchor="w")
        self.own = tk.Text(w, width=50, height=2, wrap="word", font=("Segoe UI", 9))
        self.own.pack(**pad)
        self.own.bind("<KeyRelease>", lambda e: (fix_emoji_widget(self.own), self.update_preview()))
        self.preview = tk.Label(w, font=("Consolas", 9), fg="#555", justify="left", wraplength=380)
        self.preview.pack(**pad)
        self.cd = tk.Label(w, fg="#888")
        self.cd.pack(padx=12, anchor="w")
        bar = tk.Frame(w)
        bar.pack(pady=10)
        self.yes_btn = tk.Button(bar, text="Ja", width=10, command=self.yes)
        self.yes_btn.pack(side="left", padx=6)
        tk.Button(bar, text="Nein", width=10, command=self.close).pack(side="left", padx=6)
        tk.Button(bar, text="Absender sperren", width=15, command=self.block).pack(side="left", padx=6)
        w.bind("<Escape>", lambda e: self.close())
        w.protocol("WM_DELETE_WINDOW", self.close)
        # Tippen ins eigene Textfeld hält den Countdown an
        self.own.bind("<FocusIn>", lambda e: setattr(self, "paused", True))
        self.paused = False

        if self.slots:
            active = active_slot(pkt)
            pos = next((n for n, (i, _) in enumerate(self.slots) if i == active), 0)
            self.combo.current(pos)
        self.update_preview()

        # mehrere Fenster leicht versetzt stapeln
        w.update_idletasks()
        off = 30 * (Popup.open_count % 8)
        Popup.open_count += 1
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        y = max(10, sh - w.winfo_height() - POPUP_BOTTOM_PX - off)
        w.geometry(f"+{sw - w.winfo_width() - 20 - off}+{y}")
        self.left = POPUP_TIMEOUT_S
        self.tick()

    def reply_text(self):
        own = repair_emoji(self.own.get("1.0", "end-1c")).strip()
        if own:
            return render(own, self.info)
        if not self.slots:
            return ""
        return render(self.slots[self.combo.current()][1], self.info)

    def update_preview(self):
        text = self.reply_text()
        self.yes_btn.config(state="normal" if text else "disabled")
        if not text:
            self.preview.config(text="Kein Antworttext (Tray-Menü → Antworttexte bearbeiten)")
            return
        n = len(text.encode("utf-8"))
        self.preview.config(text=text + (f"\n⚠ {n} Bytes – zu lang?" if n > MAX_BYTES else ""))

    def tick(self):
        if self.left < 0:
            return
        if self.paused:
            self.cd.config(text="Countdown angehalten")
            return
        if self.left == 0:
            return self.close()
        self.cd.config(text=f"Schließt in {self.left}s ohne Antwort")
        self.left -= 1
        self.win.after(1000, self.tick)

    def yes(self):
        text = self.reply_text()
        if text:
            send_reply(self.pkt, text)
        self.close()

    def block(self):
        block_sender(self.info["id"], self.info["name"])
        self.close()

    def close(self):
        if self.left >= 0:
            self.left = -1
            Popup.open_count = max(0, Popup.open_count - 1)
            self.win.destroy()


@single_window
class Editor:
    """Antworttexte: 9 Speicherplätze für Kanäle, 9 für DMs (mit Schalter 'ohne Bestätigung')."""

    def __init__(self):
        Editor.win = w = tk.Toplevel(ROOT)
        w.title("Mesh-Responder – Antworttexte")
        nb = ttk.Notebook(w)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.texts = {"kanal": [], "dm": []}
        self.autos = []
        for cat, title in (("kanal", "Öffentliche Kanäle"), ("dm", "Direktnachrichten")):
            f = ttk.Frame(nb, padding=8)
            nb.add(f, text=title)
            ttk.Label(f, text="Nr").grid(row=0, column=0)
            ttk.Label(f, text="Antworttext").grid(row=0, column=1, sticky="w")
            ttk.Label(f, text="Länge").grid(row=0, column=2)
            if cat == "dm":
                ttk.Label(f, text="ohne\nBestätigung", justify="center").grid(row=0, column=3, padx=6)
            for i in range(SLOTS):
                ttk.Label(f, text=str(i + 1)).grid(row=i + 1, column=0, sticky="n", padx=(0, 6), pady=3)
                t = tk.Text(f, width=56, height=3, wrap="word", font=("Segoe UI", 9))
                t.insert("1.0", settings[cat]["texte"][i])
                t.grid(row=i + 1, column=1, pady=3)
                n = tk.Label(f, width=7)
                n.grid(row=i + 1, column=2, sticky="n", pady=3)
                t.bind("<KeyRelease>", lambda e, t=t, n=n: (fix_emoji_widget(t), self.count(t, n)))
                self.count(t, n)
                self.texts[cat].append(t)
                if cat == "dm":
                    v = tk.BooleanVar(value=settings["dm"]["auto"][i])
                    ttk.Checkbutton(f, variable=v).grid(row=i + 1, column=3, sticky="n", pady=3)
                    self.autos.append(v)
        tk.Label(w, text=PLACEHOLDER_HELP, fg="#666", wraplength=600, justify="left").pack(padx=10, anchor="w")
        bar = tk.Frame(w)
        bar.pack(pady=8)
        tk.Button(bar, text="Speichern", width=12, command=self.save).pack(side="left", padx=6)
        tk.Button(bar, text="Abbrechen", width=12, command=w.destroy).pack(side="left", padx=6)

    @staticmethod
    def count(t, n):
        size = len(render(t.get("1.0", "end-1c"), SAMPLE).encode("utf-8"))
        n.config(text=f"{size} B" if size else "", fg="red" if size > MAX_BYTES else "#666")

    def save(self):
        for cat in ("kanal", "dm"):
            settings[cat]["texte"] = [repair_emoji(t.get("1.0", "end-1c").strip()) for t in self.texts[cat]]
        settings["dm"]["auto"] = [v.get() for v in self.autos]
        save_settings()
        rebuild_menu()
        self.win.destroy()


@single_window
class Setup:
    """Verbindung, Kanäle, Zeitsperre, Ruhezeiten, Hinweise."""

    def __init__(self):
        Setup.win = w = tk.Toplevel(ROOT)
        w.title("Mesh-Responder – Setup")
        w.resizable(False, False)
        w.attributes("-topmost", True)
        f = ttk.Frame(w, padding=12)
        f.pack()
        o = settings["optionen"]
        row = 0

        def section(text):
            nonlocal row
            ttk.Label(f, text=text, font=("Segoe UI", 9, "bold")).grid(row=row, column=0, columnspan=4,
                                                                         sticky="w", pady=(10 if row else 0, 2))
            row += 1

        section("Verbindung")
        ttk.Label(f, text="Adresse des Nodes (IP/Hostname):").grid(row=row, column=0, sticky="w")
        self.host = ttk.Entry(f, width=22)
        self.host.insert(0, settings["verbindung"]["host"])
        self.host.grid(row=row, column=1, columnspan=3, sticky="w")
        row += 1
        ttk.Label(f, text="Port des virtuellen Nodes:").grid(row=row, column=0, sticky="w")
        self.port = ttk.Entry(f, width=8)
        self.port.insert(0, str(settings["verbindung"]["port"]))
        self.port.grid(row=row, column=1, sticky="w")
        row += 1
        tk.Label(f, fg="#666", justify="left",
                 text="Meshhessen Client auf diesem PC: 127.0.0.1 / 4404").grid(row=row, column=0, columnspan=4,
                                                                                sticky="w")
        row += 1

        section("Kanäle, auf die reagiert wird (DMs immer)")
        names = list(dict.fromkeys(list(state["all_chans"].values()) + settings["kanaele"]))
        self.chan_vars = {}
        cf = ttk.Frame(f)
        cf.grid(row=row, column=0, columnspan=4, sticky="w")
        for n, name in enumerate(names):
            v = tk.BooleanVar(value=name in settings["kanaele"])
            idx = next((i for i, nm in state["all_chans"].items() if nm == name), None)
            ttk.Checkbutton(cf, text=f"{name} (CH{idx})" if idx is not None else name,
                            variable=v).grid(row=n // 3, column=n % 3, sticky="w", padx=(0, 12))
            self.chan_vars[name] = v
        row += 1
        if not state["all_chans"]:
            tk.Label(f, fg="#666", text="Alle Kanäle des Nodes erscheinen hier, sobald eine Verbindung besteht."
                     ).grid(row=row, column=0, columnspan=4, sticky="w")
            row += 1

        section("Zeitsperre")
        ttk.Label(f, text="Gleichen Absender nicht erneut fragen für (Minuten, 0 = aus):").grid(
            row=row, column=0, columnspan=2, sticky="w")
        self.sperre = ttk.Entry(f, width=6)
        self.sperre.insert(0, str(o["sperre_min"]))
        self.sperre.grid(row=row, column=2, sticky="w")
        row += 1

        section("Ruhezeiten")
        self.ruhe_an = tk.BooleanVar(value=o["ruhe_an"])
        ttk.Checkbutton(f, text="Ruhezeit aktiv – keine Fenster von", variable=self.ruhe_an).grid(
            row=row, column=0, sticky="w")
        self.ruhe_von = ttk.Entry(f, width=6)
        self.ruhe_von.insert(0, o["ruhe_von"])
        self.ruhe_von.grid(row=row, column=1, sticky="w")
        ttk.Label(f, text="bis").grid(row=row, column=2, sticky="w")
        self.ruhe_bis = ttk.Entry(f, width=6)
        self.ruhe_bis.insert(0, o["ruhe_bis"])
        self.ruhe_bis.grid(row=row, column=3, sticky="w")
        row += 1
        self.ruhe_auto = tk.BooleanVar(value=o["ruhe_auto"])
        ttk.Checkbutton(f, text="in der Ruhezeit automatisch mit dem aktiven Antwortplatz antworten",
                        variable=self.ruhe_auto).grid(row=row, column=0, columnspan=4, sticky="w")
        row += 1

        section("Hinweis bei neuer Nachricht")
        self.ton = tk.BooleanVar(value=o["ton"])
        ttk.Checkbutton(f, text="Ton", variable=self.ton).grid(row=row, column=0, sticky="w")
        self.benachrichtigung = tk.BooleanVar(value=o["benachrichtigung"])
        ttk.Checkbutton(f, text="Windows-Benachrichtigung", variable=self.benachrichtigung).grid(
            row=row, column=1, columnspan=3, sticky="w")
        row += 1

        self.err = tk.Label(f, fg="#c62828")
        self.err.grid(row=row, column=0, columnspan=4, sticky="w")
        row += 1
        bar = tk.Frame(f)
        bar.grid(row=row, column=0, columnspan=4, pady=(8, 0))
        tk.Button(bar, text="Speichern", width=12, command=self.save).pack(side="left", padx=6)
        tk.Button(bar, text="Abbrechen", width=12, command=w.destroy).pack(side="left", padx=6)
        w.bind("<Return>", lambda e: self.save())
        w.bind("<Escape>", lambda e: w.destroy())
        self.host.focus_set()

    def save(self):
        host = self.host.get().strip()
        try:
            port = int(self.port.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            return self.err.config(text="Port muss eine Zahl zwischen 1 und 65535 sein.")
        if not host:
            return self.err.config(text="Bitte eine Adresse eingeben.")
        try:
            sperre = int(self.sperre.get().strip() or 0)
            if sperre < 0:
                raise ValueError
        except ValueError:
            return self.err.config(text="Zeitsperre: ganze Minuten, 0 = aus.")
        try:
            parse_hhmm(self.ruhe_von.get())
            parse_hhmm(self.ruhe_bis.get())
        except ValueError:
            return self.err.config(text="Ruhezeiten im Format HH:MM, z. B. 22:00.")

        reconnect = (host, port) != (settings["verbindung"]["host"], int(settings["verbindung"]["port"]))
        settings["verbindung"] = {"host": host, "port": port}
        settings["kanaele"] = [n for n, v in self.chan_vars.items() if v.get()]
        settings["optionen"].update(sperre_min=sperre, ruhe_an=self.ruhe_an.get(),
                                    ruhe_von=self.ruhe_von.get().strip(), ruhe_bis=self.ruhe_bis.get().strip(),
                                    ruhe_auto=self.ruhe_auto.get(), ton=self.ton.get(),
                                    benachrichtigung=self.benachrichtigung.get())
        save_settings()
        log_csv("Setup geändert", text=f"{host}:{port} | Kanäle: {', '.join(settings['kanaele']) or '–'}")
        apply_channel_filter()
        if state["connected"]:
            state["status"] = "Verbunden – " + (", ".join(f"{n} (CH{i})" for i, n in state["chans"].items())
                                                or "kein Kanal gewählt")
        if reconnect:
            state["reconnect"] = True
            wake.set()
        rebuild_menu()
        self.win.destroy()


def block_sender(node_id, name):
    settings["sperrliste"][node_id] = name
    save_settings()
    log_csv("Absender gesperrt", {"id": node_id, "name": name})


@single_window
class Blocklist:
    """Sperrliste: Absender, auf die nicht reagiert wird."""

    def __init__(self):
        Blocklist.win = w = tk.Toplevel(ROOT)
        w.title("Mesh-Responder – Sperrliste")
        w.attributes("-topmost", True)
        f = ttk.Frame(w, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Auf diese Absender wird nicht reagiert (nur Log-Eintrag):").pack(anchor="w")
        self.lb = tk.Listbox(f, width=48, height=10)
        self.lb.pack(fill="both", expand=True, pady=4)
        add = ttk.Frame(f)
        add.pack(fill="x")
        ttk.Label(add, text="Node-ID:").pack(side="left")
        self.entry = ttk.Entry(add, width=14)
        self.entry.pack(side="left", padx=4)
        tk.Button(add, text="Hinzufügen", command=self.add).pack(side="left")
        tk.Label(f, fg="#666", text="Tipp: Absender auch per Rechtsklick im Log oder im Nachrichtenfenster sperren."
                 ).pack(anchor="w", pady=(4, 0))
        bar = tk.Frame(f)
        bar.pack(pady=(8, 0))
        tk.Button(bar, text="Entfernen", width=12, command=self.remove).pack(side="left", padx=6)
        tk.Button(bar, text="Schließen", width=12, command=w.destroy).pack(side="left", padx=6)
        self.load()

    def load(self):
        self.lb.delete(0, "end")
        self.ids = list(settings["sperrliste"])
        for nid in self.ids:
            self.lb.insert("end", f"{settings['sperrliste'][nid]}  ({nid})")

    def add(self):
        nid = self.entry.get().strip()
        if not nid:
            return
        if not nid.startswith("!"):
            nid = "!" + nid
        node = (state["iface"].nodes or {}).get(nid, {}) if state["iface"] else {}
        block_sender(nid, node.get("user", {}).get("longName", nid))
        self.entry.delete(0, "end")
        self.load()

    def remove(self):
        for i in reversed(self.lb.curselection()):
            nid = self.ids[i]
            log_csv("Absender entsperrt", {"id": nid, "name": settings["sperrliste"].pop(nid)})
        save_settings()
        self.load()


@single_window
class LogView:
    """Log als Tabelle + Statistik."""

    def __init__(self):
        LogView.win = w = tk.Toplevel(ROOT)
        w.title("Mesh-Responder – Log")
        w.geometry("1280x560")
        nb = ttk.Notebook(w)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        frame = ttk.Frame(nb)
        nb.add(frame, text="Log")
        self.tree = t = ttk.Treeview(frame, columns=CSV_HEADER, show="headings")
        widths = {"Zeit": 135, "Ereignis": 190, "Kanal": 85, "Node-ID": 85, "Name": 160, "Hops": 40,
                  "Text": 380, "SNR": 45, "RSSI": 45, "Weg": 50}
        for c in CSV_HEADER:
            t.heading(c, text=c)
            t.column(c, width=widths[c], stretch=(c == "Text"),
                     anchor="center" if c in ("Hops", "SNR", "RSSI", "Weg") else "w")
        t.tag_configure("out", foreground="#1a7f37")
        t.tag_configure("err", foreground="#c62828")
        t.tag_configure("sys", foreground="#888")
        sb = ttk.Scrollbar(frame, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.menu = tk.Menu(w, tearoff=0)
        t.bind("<Button-3>", self.context)

        stat = ttk.Frame(nb, padding=6)
        nb.add(stat, text="Statistik")
        self.st_day = self.stat_table(stat, "Pro Tag und Kanal",
                                      ["Datum", "Kanal", "Empfangen", "Beantwortet", "Ø Hops"], 0)
        self.st_chan = self.stat_table(stat, "Pro Kanal (gesamt)",
                                       ["Kanal", "Empfangen", "davon MQTT", "Beantwortet", "Ø Hops", "Ø SNR"], 1)
        self.st_who = self.stat_table(stat, "Häufigste Absender",
                                      ["Name", "Node-ID", "Nachrichten", "Ø Hops", "Zuletzt"], 2)
        stat.columnconfigure((0, 1, 2), weight=1)
        stat.rowconfigure(1, weight=1)

        bar = tk.Frame(w)
        bar.pack(pady=(0, 8))
        tk.Button(bar, text="Aktualisieren", width=14, command=self.load).pack(side="left", padx=6)
        tk.Button(bar, text="In Excel öffnen", width=14,
                  command=lambda: CSV_FILE.exists() and os.startfile(CSV_FILE)).pack(side="left", padx=6)
        tk.Button(bar, text="Ordner öffnen", width=14, command=lambda: os.startfile(HERE)).pack(side="left", padx=6)
        self.load()

    @staticmethod
    def stat_table(parent, title, cols, column):
        ttk.Label(parent, text=title, font=("Segoe UI", 9, "bold")).grid(row=0, column=column, sticky="w", padx=4)
        t = ttk.Treeview(parent, columns=cols, show="headings", height=18)
        widths = {"Datum": 85, "Kanal": 95, "Name": 140, "Node-ID": 80, "Zuletzt": 85, "Ø Hops": 55, "Ø SNR": 55, "davon MQTT": 82}
        for c in cols:
            t.heading(c, text=c)
            t.column(c, width=widths.get(c, 72), stretch=c in ("Kanal", "Name"),
                     anchor="w" if c in ("Datum", "Kanal", "Name", "Node-ID", "Zuletzt") else "center")
        t.grid(row=1, column=column, sticky="nsew", padx=4)
        return t

    def context(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        vals = self.tree.item(row, "values")
        nid, name = vals[3], vals[4]
        self.menu.delete(0, "end")
        if not nid:
            return
        if nid in settings["sperrliste"]:
            self.menu.add_command(label=f"{name} entsperren", command=lambda: self.unblock(nid))
        else:
            self.menu.add_command(label=f"{name} sperren", command=lambda: (block_sender(nid, name), self.load()))
        self.menu.tk_popup(event.x_root, event.y_root)

    def unblock(self, nid):
        log_csv("Absender entsperrt", {"id": nid, "name": settings["sperrliste"].pop(nid, nid)})
        save_settings()
        self.load()

    def load(self):
        rows = read_csv_rows()
        self.tree.delete(*self.tree.get_children())
        for r in reversed(rows):
            r = list(r)
            r[6] = r[6].replace("\n", " ⏎ ")
            ev = r[1]
            tag = ("err" if "Fehler" in ev else "out" if "esendet" in ev and not ev.startswith("Nicht")
                   else "" if ev == "Empfangen" else "sys")
            self.tree.insert("", "end", values=r, tags=(tag,))
        self.load_stats(rows)

    def load_stats(self, rows):
        def num(v):
            try:
                return float(v)
            except ValueError:
                return None

        def avg(values):
            values = [v for v in values if v is not None]
            return f"{sum(values) / len(values):.1f}" if values else "–"

        day = defaultdict(lambda: {"in": 0, "out": 0, "hops": []})
        chan = defaultdict(lambda: {"in": 0, "mqtt": 0, "out": 0, "hops": [], "snr": []})
        who = defaultdict(lambda: {"name": "", "n": 0, "hops": [], "last": ""})
        for zeit, ev, kanal, nid, name, hops, _text, snr, _rssi, weg in rows:
            if not kanal:
                continue
            key = (zeit[:10], kanal)
            if ev in ("Empfangen", "MQTT ignoriert"):
                for d in (day[key], chan[kanal]):
                    d["in"] += 1
                    d["hops"].append(num(hops))
                if weg == "MQTT":
                    chan[kanal]["mqtt"] += 1
                else:
                    chan[kanal]["snr"].append(num(snr))     # SNR nur für Funkempfang sinnvoll
                w = who[nid]
                w["name"], w["n"], w["last"] = name, w["n"] + 1, zeit
                w["hops"].append(num(hops))
            elif "esendet" in ev and not ev.startswith("Nicht"):
                day[key]["out"] += 1
                chan[kanal]["out"] += 1
        for t in (self.st_day, self.st_chan, self.st_who):
            t.delete(*t.get_children())
        for (d, k), v in sorted(day.items(), reverse=True):
            self.st_day.insert("", "end", values=(d, k, v["in"], v["out"], avg(v["hops"])))
        for k, v in sorted(chan.items(), key=lambda kv: -kv[1]["in"]):
            self.st_chan.insert("", "end", values=(k, v["in"], v["mqtt"], v["out"], avg(v["hops"]), avg(v["snr"])))
        for nid, v in Counter({k: v["n"] for k, v in who.items()}).most_common(50):
            v = who[nid]
            self.st_who.insert("", "end", values=(v["name"], nid, v["n"], avg(v["hops"]), v["last"][5:16]))


# --- Tray ------------------------------------------------------------------------------
@single_window
class InfoBox:
    def __init__(self):
        InfoBox.win = w = tk.Toplevel(ROOT)
        w.title("Info")
        w.resizable(False, False)
        w.attributes("-topmost", True)
        f = ttk.Frame(w, padding=16)
        f.pack()
        tk.Label(f, text=f"Autoresponder  Version {VERSION}  /  {VERSION_DATE}",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w")
        for line in ("für Virtual Meshtastic Nodes (z. B. Mesh Hessen Client 1.6.4)",
                     "",
                     "Nutzung frei – Public Domain (Unlicense, unlicense.org).",
                     "Ohne Gewährleistung.",
                     "",
                     "Rückfragen an Meshtastic #EPP1"):
            tk.Label(f, text=line, justify="left").pack(anchor="w")
        tk.Button(f, text="OK", width=10, command=w.destroy).pack(pady=(12, 0))
        w.bind("<Return>", lambda e: w.destroy())
        w.bind("<Escape>", lambda e: w.destroy())


def tray_image(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 61, 61), radius=8, fill=color)
    try:
        font = ImageFont.truetype("arialbd.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    d.text((32, 33), "M", fill="black", font=font, anchor="mm")
    return img


ICON_OK = tray_image((255, 204, 0))       # gelb = verbunden
ICON_OFF = tray_image((150, 150, 150))    # grau = nicht verbunden


def slot_items(get, set_, cat):
    """9 Radio-Einträge für einen Antwortplatz; get()/set_(i) lesen/setzen den aktiven Platz."""
    def item(i):
        def text(_):
            auto = " ⚡ sofort" if cat == "dm" and settings["dm"]["auto"][i] else ""
            return f"{i + 1}: {first_line(settings[cat]['texte'][i])}{auto}"
        return pystray.MenuItem(text, lambda icon, _: set_(i), checked=lambda _: get() == i, radio=True,
                                enabled=lambda _: bool(settings[cat]["texte"][i].strip()))
    return [item(i) for i in range(SLOTS)]


def set_option(key, value=None):
    o = settings["optionen"]
    o[key] = (not o[key]) if value is None else value
    save_settings()
    if ICON is not None:
        ICON.update_menu()


def build_menu():
    def set_dm(i):
        settings["dm"]["aktiv"] = i
        save_settings()
        ICON.update_menu()

    def chan_menu(name):
        apk = settings["kanal"]["aktiv_pro_kanal"]

        def set_(i):
            apk[name] = i
            save_settings()
            ICON.update_menu()
        return pystray.MenuItem(name, pystray.Menu(
            *slot_items(lambda: apk.get(name, settings["kanal"]["aktiv"]), set_, "kanal")))

    def set_active(value):
        def handler(icon, _):
            state["active"] = value
            log.info("Responder %s", "aktiv" if value else "inaktiv")
        return handler

    def quit_app(icon, _):
        state["stop"] = True
        wake.set()
        icon.stop()
        ROOT.after(0, ROOT.destroy)

    chans = list(state["chans"].values()) or settings["kanaele"]
    return pystray.Menu(
        pystray.MenuItem(lambda _: state["status"][:60], None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Aktiv", set_active(True), checked=lambda _: state["active"], radio=True),
        pystray.MenuItem("Inaktiv", set_active(False), checked=lambda _: not state["active"], radio=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Antwort Kanäle", pystray.Menu(*[chan_menu(n) for n in chans])
                         if chans else None, enabled=bool(chans)),
        pystray.MenuItem("Antwort DMs", pystray.Menu(
            *slot_items(lambda: settings["dm"]["aktiv"], set_dm, "dm"))),
        pystray.MenuItem("Antworttexte bearbeiten …", lambda icon, _: events.put(Editor.show)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Auf Smileys reagieren", lambda icon, _: set_option("smileys"),
                         checked=lambda _: settings["optionen"]["smileys"]),
        pystray.MenuItem("Auf MQTT-Nachrichten reagieren", lambda icon, _: set_option("mqtt"),
                         checked=lambda _: settings["optionen"]["mqtt"]),
        pystray.MenuItem("Ruhezeit", lambda icon, _: set_option("ruhe_an"),
                         checked=lambda _: settings["optionen"]["ruhe_an"]),
        pystray.MenuItem("Sperrliste …", lambda icon, _: events.put(Blocklist.show)),
        pystray.MenuItem("Log und Statistik …", lambda icon, _: events.put(LogView.show)),
        pystray.MenuItem("Setup …", lambda icon, _: events.put(Setup.show)),
        pystray.MenuItem("Info …", lambda icon, _: events.put(InfoBox.show)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Beenden", quit_app),
    )


def rebuild_menu():
    if ICON is not None:
        ICON.menu = build_menu()


def main():
    global ROOT, ICON
    ROOT = root = tk.Tk()
    root.withdraw()
    migrate_csv()

    ICON = icon = pystray.Icon("mesh_responder", ICON_OFF, "Mesh-Responder", menu=build_menu())
    icon.run_detached()

    if DEMO:
        state.update(connected=True, status="Demo", all_chans={0: "ShortSlow", 1: "HessenTest", 2: "Mesh Hessen"})
        apply_channel_filter()
        rebuild_menu()
        for p in ({"id": 1, "from": 0x12345678, "fromId": "!12345678", "channel": 1, "rxSnr": -7.25,
                   "rxRssi": -112, "hopStart": 3, "hopLimit": 0, "decoded": {"text": "Test"}},
                  {"id": 3, "from": 0x87654321, "fromId": "!87654321", "to": 0x0000abcd,
                   "channel": 0, "pkiEncrypted": True, "hopStart": 3, "hopLimit": 3, "rxSnr": 12.0,
                   "rxRssi": -40, "decoded": {"text": "Ja hört"}}):
            log_csv("Empfangen", pkt_info(p, None))
            events.put(lambda p=p: handle_incoming(p))
    else:
        threading.Thread(target=mesh_loop, daemon=True).start()

    shown = {"img": None}

    def poll():
        while not events.empty():
            try:
                events.get()()
            except Exception:
                log.exception("Fehler im UI")
        img = ICON_OK if state["connected"] else ICON_OFF
        if shown["img"] is not img:
            icon.icon = shown["img"] = img
        extra = ("" if state["active"] else " (inaktiv)") + (" (Ruhezeit)" if in_quiet_time() else "")
        title = f"Mesh-Responder{extra} – {state['status']}"[:127]
        if icon.title != title:
            icon.title = title
            icon.update_menu()
        root.after(300, poll)

    poll()
    root.mainloop()


if __name__ == "__main__":
    main()
