#!/usr/bin/env python3
"""Kurvenwetter: OpenStreetMap-Auszug -> kleine Kachel-Dateien für die Webseite.

Eingabe:  GeoJSON-Text-Sequenzen aus `osmium export` (eine Zeile = ein Objekt mit allen Tags),
          dazu optional die Geofabrik-Grenzen der Regionen (.poly) und eine Länder-Datei (GeoJSON).
Ausgabe:  <out>/index.json           Verzeichnis: welche Kacheln es gibt, welches Gebiet abgedeckt ist
          <out>/r/<iy>_<ix>.json     Strecken-Daten je 0,25°-Kachel: Pässe, Tankstellen, Autobahnen,
                                     Mautstraßen, zeitliche Sperren, Tiroler Lärm-Strecken
          <out>/h/<iy>_<ix>.json     Unterkünfte und Orte je 0,5°-Kachel
          <out>/countries.json       Ländergrenzen (für Vignetten und Feiertage)

Das Skript braucht nur Python 3, keine Zusatzpakete. Was es behält, entspricht genau dem,
was die App bisher über Overpass abgefragt hat.
"""
import argparse, datetime, glob, json, math, os, re, shutil, sys

CELL_R, CELL_H = 0.25, 0.5
COND_KEYS = ('motor_vehicle:conditional', 'motorcycle:conditional', 'access:conditional',
             'vehicle:conditional', 'motorcar:conditional')
TOURISM = {'hotel', 'guest_house', 'motel', 'hostel', 'camp_site', 'chalet', 'apartment', 'alpine_hut'}
PLACES = {'village', 'town', 'city'}
S_REF = re.compile(r'^S ?\d')                                   # österreichische Schnellstraßen
NOISE_REF = re.compile(r'^[BL] ?(198|199|21|72|246|266)$')      # Tiroler 95-dB-Strecken
NOISE_BOX = (47.15, 10.15, 47.65, 10.95)                        # Süd, West, Nord, Ost
BIKER = re.compile(r'motorrad|biker|motorcycle|moto', re.I)
EUROPE = (34.0, -11.0, 62.0, 32.0)                              # nur Länder-Flächen in diesem Fenster behalten
A3_TO_A2 = {'FRA': 'FR', 'NOR': 'NO', 'KOS': 'XK'}              # Natural Earth hat hier keinen 2-Buchstaben-Code


# ---------- Geometrie ----------
def cell(lat, lon, size):
    return (math.floor((lat + 90) / size), math.floor((lon + 180) / size))


def line_cells(pts, size):
    """Alle Kacheln, die eine Linie berührt (auch lange gerade Stücke ohne Zwischenpunkt)."""
    cs = set()
    for i, (la, lo) in enumerate(pts):
        cs.add(cell(la, lo, size))
        if i:
            pla, plo = pts[i - 1]
            n = int(max(abs(la - pla), abs(lo - plo)) / (size / 4))
            for k in range(1, n + 1):
                f = k / (n + 1)
                cs.add(cell(pla + f * (la - pla), plo + f * (lo - plo), size))
    return cs


def simplify(pts, tol_m):
    """Douglas-Peucker: Punkte weglassen, die weniger als tol_m von der Linie abweichen."""
    n = len(pts)
    if n < 3:
        return pts
    kx, ky = 111320 * math.cos(math.radians(pts[0][0])), 110540
    P = [(lo * kx, la * ky) for la, lo in pts]
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        s, e = stack.pop()
        x1, y1 = P[s]
        x2, y2 = P[e]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        md, mi = 0.0, -1
        for i in range(s + 1, e):
            d = math.hypot(P[i][0] - x1, P[i][1] - y1) if L == 0 else abs(dy * P[i][0] - dx * P[i][1] + x2 * y1 - y2 * x1) / L
            if d > md:
                md, mi = d, i
        if md > tol_m:
            keep[mi] = True
            stack.append((s, mi))
            stack.append((mi, e))
    return [p for p, k in zip(pts, keep) if k]


def encode(pts):
    """Koordinaten als ganze Zahlen (Grad * 100000), jeder Punkt als Abstand zum vorigen. Spart ca. die Hälfte."""
    out, pa, po = [], 0, 0
    for la, lo in pts:
        a, o = round(la * 1e5), round(lo * 1e5)
        out += [a - pa, o - po]
        pa, po = a, o
    return out


def vertices(geom):
    """Alle Punkte einer GeoJSON-Geometrie als (lat, lon). Flächen: nur der äußere Rand."""
    t, c = geom.get('type'), geom.get('coordinates')
    if t == 'Point':
        return [(c[1], c[0])]
    if t == 'LineString':
        return [(p[1], p[0]) for p in c]
    if t == 'MultiLineString':
        return [(p[1], p[0]) for part in c for p in part]
    if t == 'Polygon':
        return [(p[1], p[0]) for p in c[0][:-1]] if c else []
    if t == 'MultiPolygon':
        return [(p[1], p[0]) for poly in c if poly for p in poly[0][:-1]]
    return []


def centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def bbox(pts):
    return [round(min(p[0] for p in pts), 4), round(min(p[1] for p in pts), 4),
            round(max(p[0] for p in pts), 4), round(max(p[1] for p in pts), 4)]


def flat(pts, nd=4):
    out = []
    for la, lo in pts:
        out += [round(la, nd), round(lo, nd)]
    return out


# ---------- Eingabe ----------
def features(paths):
    for path in paths:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip().lstrip('\x1e')
                if not line:
                    continue
                try:
                    ft = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ft, dict) and ft.get('type') == 'Feature' and ft.get('geometry'):
                    yield ft


def pick(tags, keys):
    return {k: str(tags[k]) for k in keys if tags.get(k) not in (None, '')}


def collect(paths):
    """Liest alle Objekte und behält, was die App braucht. Regionen überlappen sich an den Rändern,
    deshalb wird über die OSM-Kennung bzw. Name + Ort entdoppelt."""
    passes, fuel, hotels, places, ways = {}, {}, {}, {}, {}
    n_in = 0
    for ft in features(paths):
        n_in += 1
        geom, t, fid = ft['geometry'], ft.get('properties') or {}, str(ft.get('id') or n_in)
        gt = geom.get('type')
        try:
            pts = vertices(geom)
        except (TypeError, IndexError, KeyError):
            continue
        if not pts:
            continue

        if t.get('mountain_pass') == 'yes' and gt == 'Point':
            passes[fid] = (pts[0][0], pts[0][1], pick(t, ('mountain_pass', 'name', 'name:de', 'ele')))

        if t.get('amenity') == 'fuel':
            la, lo = centroid(pts)
            tags = pick(t, ('amenity', 'brand', 'name', 'operator', 'opening_hours', 'automated'))
            # Eine als Fläche gezeichnete Tankstelle kommt aus osmium doppelt (Linie und Fläche)
            fuel.setdefault((tags.get('brand') or tags.get('name') or '', round(la, 3), round(lo, 3)), (fid, la, lo, tags))

        if t.get('tourism') in TOURISM and t.get('name'):
            la, lo = centroid(pts)
            tags = pick(t, ('tourism', 'name', 'stars', 'website', 'contact:website', 'motorcycle', 'motorcycle_friendly'))
            if BIKER.search(str(t.get('description') or '')):
                tags['description'] = str(t['description'])[:80]
            hotels.setdefault((tags['name'], round(la, 3), round(lo, 3)), (fid, la, lo, tags))

        if t.get('place') in PLACES and gt == 'Point' and t.get('name'):
            places[fid] = (pts[0][0], pts[0][1], pick(t, ('place', 'name')))

        if gt == 'LineString' and t.get('highway') and len(pts) >= 2:
            hw, ref = str(t['highway']), str(t.get('ref') or '')
            is_mw = hw == 'motorway' or (hw == 'trunk' and S_REF.match(ref))
            is_toll = t.get('toll') == 'yes'
            cond = {k: str(t[k]) for k in COND_KEYS if str(t.get(k) or '').lower().startswith('no')}
            s, w, n, e = NOISE_BOX
            is_noise = bool(NOISE_REF.match(ref)) and s < pts[0][0] < n and w < pts[0][1] < e
            if not (is_mw or is_toll or cond or is_noise):
                continue
            tags = pick(t, ('highway', 'ref', 'name'))
            if is_toll:
                tags.update(pick(t, ('toll', 'toll:name')))
            tags.update(cond)
            ways[fid] = (pts, tags)
    return n_in, passes, fuel, hotels, places, ways


# ---------- Grenzen ----------
def read_poly(path):
    """Geofabrik-.poly-Datei -> Liste von Ringen [(lat, lon), ...]. Löcher (mit ! markiert) werden ignoriert."""
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = [l.strip() for l in f if l.strip()]
    rings, i = [], 1
    while i < len(lines):
        if lines[i] == 'END':
            i += 1
            continue
        hole, pts = lines[i].startswith('!'), []
        i += 1
        while i < len(lines) and lines[i] != 'END':
            parts = lines[i].split()
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[1]), float(parts[0])))
                except ValueError:
                    pass
            i += 1
        i += 1
        if not hole and len(pts) >= 3:
            rings.append(pts)
    return rings


def build_cover(poly_dir):
    cover, names = [], []
    for path in sorted(glob.glob(os.path.join(poly_dir, '*.poly'))):
        rings = read_poly(path)
        if rings:
            names.append(os.path.splitext(os.path.basename(path))[0])
        for ring in rings:
            ring = simplify(ring, 300)
            cover.append({'b': bbox(ring), 'r': [flat(ring, 3)]})
    return cover, names


def iso_of(props):
    for key in ('ISO_A2_EH', 'ISO_A2', 'ISO3166-1-Alpha-2', 'iso_a2'):
        v = str(props.get(key) or '')
        if re.fullmatch(r'[A-Z]{2}', v):
            return v
    for key in ('ADM0_A3', 'ISO_A3', 'ISO3166-1-Alpha-3'):
        v = str(props.get(key) or '')
        if v in A3_TO_A2:
            return A3_TO_A2[v]
    return None


def build_countries(path):
    """Länder-GeoJSON (Natural Earth 1:10 Mio.) -> {ISO: [{b: Rahmen, r: [Außenring, Löcher ...]}]}"""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    out = {}
    for ft in data.get('features', []):
        iso, geom = iso_of(ft.get('properties') or {}), ft.get('geometry') or {}
        if not iso:
            continue
        polys = [geom['coordinates']] if geom.get('type') == 'Polygon' else geom.get('coordinates', []) if geom.get('type') == 'MultiPolygon' else []
        for poly in polys:
            if not poly or len(poly[0]) < 4:
                continue
            outer = [(p[1], p[0]) for p in poly[0]]
            b = bbox(outer)
            if b[2] < EUROPE[0] or b[0] > EUROPE[2] or b[3] < EUROPE[1] or b[1] > EUROPE[3]:
                continue                                   # z. B. Französisch-Guayana
            rings = [flat(simplify(outer, 40))]
            for hole in poly[1:]:
                h = simplify([(p[1], p[0]) for p in hole], 40)
                if len(h) >= 4:
                    rings.append(flat(h))
            out.setdefault(iso, []).append({'b': b, 'r': rings})
    return out


# ---------- Ausgabe ----------
def dump(path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('inputs', nargs='+', help='GeoJSON-Text-Sequenzen aus osmium export')
    ap.add_argument('--out', required=True, help='Zielordner, z. B. site/data')
    ap.add_argument('--polys', help='Ordner mit Geofabrik-.poly-Dateien der Regionen (für die Abdeckung)')
    ap.add_argument('--countries', help='Länder-GeoJSON (Natural Earth)')
    ap.add_argument('--tolerance', type=float, default=4.0, help='Vereinfachung der Straßenlinien in Metern')
    a = ap.parse_args()

    n_in, passes, fuel, hotels, places, ways = collect(a.inputs)
    if not (passes or fuel or ways):
        sys.exit('Abbruch: In den Eingabedateien wurden keine Pässe, Tankstellen oder Straßen gefunden.')

    r_tiles, h_tiles = {}, {}
    for fid, (la, lo, tags) in passes.items():
        r_tiles.setdefault(cell(la, lo, CELL_R), {'n': [], 'w': []})['n'].append([fid, round(la, 5), round(lo, 5), tags])
    for fid, la, lo, tags in fuel.values():
        r_tiles.setdefault(cell(la, lo, CELL_R), {'n': [], 'w': []})['n'].append([fid, round(la, 5), round(lo, 5), tags])
    n_pts = 0
    for fid, (pts, tags) in ways.items():
        pts = simplify(pts, a.tolerance)
        n_pts += len(pts)
        item = [fid, encode(pts), tags]
        for c in line_cells(pts, CELL_R):
            r_tiles.setdefault(c, {'n': [], 'w': []})['w'].append(item)
    for fid, la, lo, tags in hotels.values():
        h_tiles.setdefault(cell(la, lo, CELL_H), {'n': []})['n'].append([fid, round(la, 5), round(lo, 5), tags])
    for fid, (la, lo, tags) in places.items():
        h_tiles.setdefault(cell(la, lo, CELL_H), {'n': []})['n'].append([fid, round(la, 5), round(lo, 5), tags])

    # Länder: neu bauen, wenn eine Datei da ist; sonst eine vorhandene countries.json behalten
    countries, old = None, os.path.join(a.out, 'countries.json')
    if a.countries and os.path.exists(a.countries):
        countries = build_countries(a.countries)
        if not countries:
            sys.exit('Abbruch: Die Länder-Datei enthält keine verwertbaren Länder.')
    elif os.path.exists(old):
        with open(old, encoding='utf-8') as f:
            countries = json.load(f)

    for sub in ('r', 'h'):
        shutil.rmtree(os.path.join(a.out, sub), ignore_errors=True)
        os.makedirs(os.path.join(a.out, sub))
    size = 0
    for sub, tiles in (('r', r_tiles), ('h', h_tiles)):
        for (iy, ix), content in tiles.items():
            path = os.path.join(a.out, sub, f'{iy}_{ix}.json')
            dump(path, {k: v for k, v in content.items() if v})
            size += os.path.getsize(path)
    if countries:
        dump(old, countries)

    cover, regions = build_cover(a.polys) if a.polys else ([], [])
    stats = {'passes': len(passes), 'fuel': len(fuel), 'ways': len(ways), 'hotels': len(hotels), 'places': len(places)}
    dump(os.path.join(a.out, 'index.json'), {
        'v': 1, 'built': datetime.date.today().isoformat(), 'cellR': CELL_R, 'cellH': CELL_H,
        'r': sorted(f'{iy}_{ix}' for iy, ix in r_tiles), 'h': sorted(f'{iy}_{ix}' for iy, ix in h_tiles),
        'cover': cover, 'regions': regions, 'countries': bool(countries), 'stats': stats})

    print(f'{n_in} Objekte gelesen -> {stats}')
    print(f'{len(r_tiles)} Strecken-Kacheln, {len(h_tiles)} Unterkunfts-Kacheln, {n_pts} Linienpunkte, zusammen {size / 1e6:.1f} MB')
    print(f'Abdeckung: {len(cover)} Fläche(n) aus {len(regions)} Region(en); Länder: {len(countries) if countries else "keine Datei"}')


if __name__ == '__main__':
    main()
