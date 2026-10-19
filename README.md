# Autoresponder für Meshtastic (Virtual Node)

**Version 1.1.1 / Oktober 2026** – Public Domain ([Unlicense](LICENSE))

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
- Antwort immer dorthin, wo die Nachricht herkam (Kanal oder DM, PKI-verschlüsselt wie eingehend)
- 9 Antworttexte für Kanäle, 9 für DMs; aktiver Platz pro Kanal wählbar
- Platzhalter: `{name} {short} {id} {wo} {kanal} {hops} {snr} {rssi} {text} {zeit}`
- Eigener Text direkt im Nachrichtenfenster möglich
- DM-Plätze optional „ohne Bestätigung“ (sofortige Antwort)
- Smiley-/Reaktions-Filter, Sperrliste, Zeitsperre pro Absender, Ruhezeiten
- Filter für Nachrichten, die über MQTT (Internet) statt per Funk kamen
- Ton und Windows-Benachrichtigung bei neuer Nachricht
- Log als CSV (Excel) und als Tabelle mit Statistik im Programm
- Setup im Programm: Adresse/Port des Nodes, Kanäle, Zeitsperre, Ruhezeiten
- Emoji-Korrektur für die Tk-Eingabe unter Windows
- Wachhund: friert das Programm ein, startet es sich nach 3 Minuten selbst neu

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

## Dateien

| Datei | Inhalt |
|---|---|
| `mesh_responder.py` | das Programm |
| `start_mesh_responder.bat` | Starter (legt beim ersten Mal `.venv` an) |
| `Anleitung.txt` | Bedienungsanleitung |
| `mesh_responder.json` | eigene Einstellungen – wird automatisch angelegt, nicht im Repo |
| `mesh_responder_log.csv`, `mesh_responder.log` | Logs – nicht im Repo |

## Lizenz und Kontakt

Gemeinfrei (Public Domain, siehe [LICENSE](LICENSE)). Nutzung frei, ohne Gewährleistung.
Rückfragen an Meshtastic **#EPP1**.
