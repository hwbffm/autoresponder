# Autoresponder für Meshtastic (Virtual Node)

**Version 1.3.2 / Oktober 2026** – Public Domain ([Unlicense](LICENSE))

Kleines Windows-Tray-Programm, das über den **Virtual Node** eines Meshtastic-Clients
(z. B. [Mesh Hessen Client](https://www.meshhessen.de) 1.6.4) im Mesh mithört. Bei jeder
Nachricht in den gewählten Kanälen und bei jeder Direktnachricht klappt ein kleines
Fenster auf – mit **Ja** geht eine vorbereitete Antwort (z. B. eine Empfangsbestätigung)
in denselben Kanal bzw. als DM an den Absender zurück.

```
In Standort angekommen ✅
Nodename | NODE | !12345678
Empfangen: CH1 | Hops: 3
```

## Funktionen

- Tray-Icon: gelbes **M** = verbunden, grau = keine Verbindung
- Reagiert auf frei wählbare Kanäle (z. B. HessenTest, ShortSlow) und auf DMs
- Antwort dorthin, wo die Nachricht herkam (Kanal oder DM, PKI-verschlüsselt wie eingehend)
- **Antwort als DM statt im Kanal**, im Setup pro Kanal wählbar – Standard für ShortSlow und
  Mesh Hessen, wie von [Mesh Hessen](https://www.meshhessen.de) erbeten (Kanäle frei halten,
  getestet wird in HessenTest)
- **Bot-Erkennung:** Hessenbot, Monitor-Nodes, 🤖-Nachrichten und typische Autoresponder-Texte
  (z. B. Hops + SNR) werden erkannt und nie beantwortet – keine Ping-Pong-Schleifen
- 9 Antworttexte für Kanäle, 9 für DMs; aktiver Platz pro Kanal wählbar
- Platzhalter: `{name} {short} {id} {wo} {kanal} {hops} {snr} {rssi} {text} {zeit} {weg}`
- Längenprüfung: max. 200 Byte im Kanal, 185 Byte als DM – zu lange Antworten sperren „Ja“,
  automatische Antworten werden am Wortende gekürzt
- Läuft nur einmal: ein zweiter Start wird mit Hinweis abgelehnt
- DM-Zustellung im Log (zugestellt / weitergeleitet / nicht zugestellt mit Grund); keine DM an
  Absender, deren Schlüssel der Node noch nicht kennt
- Eigener Text direkt im Nachrichtenfenster möglich
- DM-Plätze optional „ohne Bestätigung“ (sofortige Antwort)
- **Tray-Schalter „DMs automatisch beantworten“:** alle Direktnachrichten sofort mit dem aktiven
  DM-Text beantworten – mit Mindestabstand pro Absender als Schutz vor Endlosschleifen zwischen
  zwei Autorespondern
- Smiley-/Reaktions-Filter, Sperrliste, Zeitsperre pro Absender, Ruhezeiten
- Filter für Nachrichten, die über MQTT (Internet) statt per Funk kamen
- Ton und Windows-Benachrichtigung bei neuer Nachricht
- Log als CSV (Excel) und als Tabelle mit Statistik im Programm
- Setup im Programm: Adresse/Port des Nodes, Kanäle, Antwort als DM, Bots, Zeitsperre, Ruhezeiten
- Emoji-Korrektur für die Tk-Eingabe unter Windows
- Wachhund: friert das Programm ein, startet es sich nach 3 Minuten selbst neu
- Tray-Symbol wird alle 20 s geprüft und bei Bedarf neu angelegt (z. B. wenn die Taskleiste
  beim Hochfahren noch nicht bereit war)

## Voraussetzungen

- Windows 10/11, Python 3.10 oder neuer (im PATH)
- Meshtastic-Client mit aktiviertem **Virtual Node** (TCP, Standard-Port 4404)
- Der Client muss **per WLAN/TCP** mit dem Node verbunden sein (nicht Bluetooth/USB)

## Start

1. Ordner an einen festen Platz kopieren.
2. `start_mesh_responder.bat` doppelklicken – beim ersten Start wird eine Python-Umgebung
   (`.venv`) mit `meshtastic`, `pystray` und `pillow` angelegt.
3. Über das Tray-Menü → **Antworttexte bearbeiten …** den eigenen Standort eintragen.

Ausführliche Beschreibung, Einstellungen im Mesh Hessen Client und Fehlersuche:
**[Anleitung.txt](Anleitung.txt)**

Test ohne Funk: `python mesh_responder.py --demo` (simuliert Nachrichten, sendet nichts).

## Als EXE (ohne Python)

**Fertig gebaut zum Herunterladen:** [`release/MeshResponder.exe`](https://github.com/hwbffm/autoresponder/raw/main/release/MeshResponder.exe) (Version 1.3.2, ca. 25 MB)  
SHA256: `0af378166d7ed94810c73786b703f16aaa9ed8e075808d5ae106cae704a51809`  (auch in `release/MeshResponder.exe.sha256`; prüfen mit `certutil -hashfile MeshResponder.exe SHA256`)

`build_exe.bat` baut mit PyInstaller eine einzelne `dist\MeshResponder.exe` (ca. 25 MB), die ohne
installiertes Python läuft. Einstellungen und Logs legt die EXE **neben sich** ab.
Die EXE ist nicht digital signiert; Windows Defender/SmartScreen kann beim ersten Start warnen.

## Keepalive-Proxy (optional)

Ein Meshtastic-Node schließt eine TCP-Verbindung (WLAN/Ethernet), wenn vom Client 15 Minuten lang
kein einziges Byte kommt – eine Schutzfunktion der Firmware, denn der Node nimmt nur einen TCP-Client
gleichzeitig an. Es zählt nur, was der Client an den Node schickt; empfangene Nachrichten zählen nicht.
Die offiziellen Meshtastic-Apps senden deshalb regelmäßig einen Heartbeat. Ein Client, der im Leerlauf
nichts sendet (z. B. Meshhessen Client 1.6.4.x), wird alle 15 Minuten getrennt und verbindet sich
neu – und jedes Mal startet sein Virtual Node neu, der Autoresponder verliert kurz die Verbindung.
Zu erkennen im Client-Log an Trennungen im Abstand von genau 15 Minuten.
Abhilfe schafft der **[Keepalive-Proxy](https://github.com/hwbffm/keepalive-proxy)** (eigenes Repository): Er sitzt zwischen Client
und Node, leitet alles unverändert weiter und schickt dem Node bei Stille einen Heartbeat. Im Client
dann Host `127.0.0.1`, Port `4405` eintragen. Statusnachrichten des Proxys (Absender „Ping Proxy“)
beantwortet der Autoresponder nicht.

## Dateien

| Datei | Inhalt |
|---|---|
| `mesh_responder.py` | das Programm |
| `start_mesh_responder.bat` | Starter (legt beim ersten Mal `.venv` an) |
| `release/MeshResponder.exe` | fertig gebaute EXE (+ `.sha256`) |
| `build_exe.bat`, `make_icon.py` | bauen `dist\MeshResponder.exe` selbst |
| `Anleitung.txt` | Bedienungsanleitung |
| `mesh_responder.json` | eigene Einstellungen – wird automatisch angelegt, nicht im Repo |
| `mesh_responder_log.csv`, `mesh_responder.log` | Logs – nicht im Repo |

## Danke

Beide Programme bauen auf dem **[Meshhessen Client](https://github.com/SMLunchen/mh_windowsclient)**
von SMLunchen auf ([Handbuch](https://meshhessenclient.de/handbuch/)). Ein großes Dankeschön an
SMLunchen und alle Mitwirkenden für die tolle Arbeit! Ohne den Virtual Node des Clients gäbe es diesen Autoresponder nicht. Der Client wird mit jedem Release
besser, zuletzt 1.7.0 mit Funk-Echo, Telemetrie-Dashboard und 3D-Gelände auf der Karte. Danke auch
an die **[Mesh-Hessen-Community](https://www.meshhessen.de)** für Netz, MQTT-Server und Dokumentation.

## Lizenz und Kontakt

Gemeinfrei (Public Domain, siehe [LICENSE](LICENSE)). Nutzung frei, ohne Gewährleistung.
Rückfragen an Meshtastic **#EPP1**.
