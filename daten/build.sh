#!/usr/bin/env bash
# Kurvenwetter: OSM-Daten für die Webseite aufbereiten.
# Lädt je Region den Geofabrik-Auszug, filtert mit osmium heraus, was die App braucht,
# und baut daraus die Kachel-Dateien. Aufruf:  bash daten/build.sh [Zielordner]
# Braucht: curl, osmium-tool, python3. Läuft so auf GitHub Actions (siehe .github/workflows/daten.yml).
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${1:-site/data}"
WORK="${WORK:-work}"
mkdir -p "$OUT" "$WORK/geo" "$WORK/poly"
rm -f "$WORK"/geo/*.geojsonseq "$WORK"/poly/*.poly

# Genau das, was die App bisher über Overpass abgefragt hat
FILTER=(
  n/mountain_pass=yes
  w/highway=motorway,trunk
  w/toll=yes
  nwr/amenity=fuel
  w/motor_vehicle:conditional w/motorcycle:conditional w/access:conditional w/vehicle:conditional w/motorcar:conditional
  "w/ref=B198,B 198,B199,B 199,L21,L 21,L72,L 72,L246,L 246,L266,L 266"
  nwr/tourism=hotel,guest_house,motel,hostel,camp_site,chalet,apartment,alpine_hut
  n/place=village,town,city
)

mapfile -t REGIONS < <(grep -v '^[[:space:]]*#' daten/regionen.txt | grep -v '^[[:space:]]*$' | tr -d '\r')
[ "${#REGIONS[@]}" -gt 0 ] || { echo "daten/regionen.txt enthält keine Region."; exit 1; }

for region in "${REGIONS[@]}"; do
  name="${region//\//_}"
  echo "== $region"
  curl -fL --retry 3 --retry-delay 15 -sS -o "$WORK/in.osm.pbf" "https://download.geofabrik.de/$region-latest.osm.pbf"
  curl -fL --retry 3 --retry-delay 15 -sS -o "$WORK/poly/$name.poly" "https://download.geofabrik.de/$region.poly"
  ls -lh "$WORK/in.osm.pbf" | awk '{print "   Auszug: " $5}'
  osmium tags-filter "$WORK/in.osm.pbf" "${FILTER[@]}" -o "$WORK/f.osm.pbf" --overwrite
  osmium export "$WORK/f.osm.pbf" -f geojsonseq --add-unique-id=type_id -o "$WORK/geo/$name.geojsonseq" --overwrite
  rm -f "$WORK/in.osm.pbf" "$WORK/f.osm.pbf"
done

# Ländergrenzen (Natural Earth, gemeinfrei). Zwei Quellen, falls eine nicht erreichbar ist.
COUNTRIES="$WORK/countries.geojson"
curl -fL --retry 3 -sS -o "$COUNTRIES" "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson" \
  || curl -fL --retry 3 -sS -o "$COUNTRIES" "https://raw.githubusercontent.com/datasets/geo-countries/main/data/countries.geojson" \
  || { echo "Ländergrenzen konnten nicht geladen werden."; rm -f "$COUNTRIES"; [ -f "$OUT/countries.json" ] || exit 1; }

python3 daten/build_osm.py --out "$OUT" --polys "$WORK/poly" --countries "$COUNTRIES" "$WORK"/geo/*.geojsonseq
