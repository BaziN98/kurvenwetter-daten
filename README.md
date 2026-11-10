# Kurvenwetter – Kartendaten

Aufbereitete OpenStreetMap-Daten für Kurvenwetter: Pässe, Tankstellen, Autobahnen, Mautstraßen, zeitliche Sperren und Unterkünfte als kleine Kachel-Dateien.

- `daten/regionen.txt` – Liste der Regionen (Geofabrik-Auszüge)
- `daten/build.sh` – lädt je Region den Auszug und filtert mit `osmium`
- `daten/build_osm.py` – baut die Kacheln (reines Python 3)
- `.github/workflows/daten.yml` – läuft monatlich, bei Änderungen und auf Knopfdruck und stellt die Daten über GitHub Pages bereit

Daten: © OpenStreetMap-Mitwirkende, Open Database License (ODbL). Ländergrenzen: Natural Earth (gemeinfrei).
