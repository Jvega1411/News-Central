"""Generate static/world.svg (Equal Earth projection) from Natural Earth 110m.

Natural Earth is public domain. Equal Earth projection: Savric, Patterson,
Jenny (2018), closed-form forward equations. Each country becomes one
<path id="XX"> keyed by ISO alpha-2 (ISO_A2_EH, which fixes FR/NO/etc).
"""
import json, math, sys

SRC = sys.argv[1]
OUT = sys.argv[2]

A1, A2, A3, A4 = 1.340264, -0.081106, 0.000893, 0.003796
SQ3 = math.sqrt(3)


def equal_earth(lon_deg, lat_deg):
    lam = math.radians(lon_deg)
    phi = math.radians(lat_deg)
    th = math.asin(max(-1.0, min(1.0, (SQ3 / 2.0) * math.sin(phi))))
    x = (2.0 * SQ3 * lam * math.cos(th)) / (
        3.0 * (9.0 * A4 * th**8 + 7.0 * A3 * th**6 + 3.0 * A2 * th**2 + A1))
    y = A1 * th + A2 * th**3 + A3 * th**7 + A4 * th**9
    return x, y


def iso_of(props):
    for key in ("ISO_A2_EH", "ISO_A2"):
        v = props.get(key)
        if v and v != "-99":
            return v.upper()
    return None


data = json.load(open(SRC, encoding="utf-8"))
feats = data["features"]

# Project everything first to find bounds.
projected = []  # (iso, [ [ (x,y),... ] ring, ... ])
minx = miny = 1e9
maxx = maxy = -1e9
for f in feats:
    iso = iso_of(f["properties"])
    if not iso:
        continue
    geom = f["geometry"]
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    rings = []
    for poly in polys:
        for ring in poly:
            pts = []
            for lon, lat in ring:
                x, y = equal_earth(lon, lat)
                pts.append((x, y))
                if x < minx: minx = x
                if x > maxx: maxx = x
                if y < miny: miny = y
                if y > maxy: maxy = y
            if len(pts) >= 3:
                rings.append(pts)
    if rings:
        projected.append((iso, rings))

# Map projected coords to an SVG viewBox of width W (y flipped, SVG y-down).
W = 1000.0
scale = W / (maxx - minx)
H = (maxy - miny) * scale


def tx(x):
    return (x - minx) * scale


def ty(y):
    return (maxy - y) * scale  # flip


# Merge rings belonging to the same ISO (a country may appear once already,
# but guard against duplicate features by accumulating).
by_iso = {}
for iso, rings in projected:
    by_iso.setdefault(iso, []).extend(rings)

parts = []
parts.append(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %.0f %.0f" '
    'preserveAspectRatio="xMidYMid meet" role="img" aria-label="World map">' % (W, H))
parts.append('<!-- Equal Earth projection. Geometry: Natural Earth (public domain). -->')
for iso in sorted(by_iso):
    d = []
    for ring in by_iso[iso]:
        d.append("M")
        d.append("%.2f %.2f" % (tx(ring[0][0]), ty(ring[0][1])))
        for x, y in ring[1:]:
            d.append("L%.2f %.2f" % (tx(x), ty(y)))
        d.append("Z")
    parts.append('<path id="%s" d="%s"/>' % (iso, " ".join(d)))
parts.append("</svg>")

open(OUT, "w", encoding="utf-8").write("\n".join(parts))
print("wrote", OUT)
print("countries:", len(by_iso))
print("viewBox: 0 0 %.0f %.0f" % (W, H))
for probe in ("ES", "FR", "US", "CN", "GB", "RU", "NO", "TW"):
    print(probe, "present" if probe in by_iso else "MISSING")
