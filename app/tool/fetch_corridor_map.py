"""One-off: fetch + stitch real OSM tiles covering the Highway 15 corridor
into a single offline map-pack asset for the Flutter app. Not part of the
backend service — run once, output goes to app/assets/.
"""
import math
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image

ENTRY = (29.8320, 35.9910)
EXIT = (29.3350, 36.0240)
# The corridor itself is a near-straight north-south line, so a uniform
# margin left a tile pack only ~0.26deg wide — a thin strip that shows
# empty bars either side once the map viewport zooms out far enough to
# fit both gates on screen. Widen east-west specifically so there's real
# terrain either side of the road at any zoom the app actually uses.
LAT_MARGIN_DEG = 0.08
# Requested symmetrically around the corridor, but zoom-level tile
# boundaries don't land symmetrically once snapped outward — the stitched
# image ends up wider on one side than the other. Generous margin here
# absorbs that rather than trying to correct for it after the fact.
LON_MARGIN_DEG = 0.6
ZOOM = 12
TILE = 256
UA = "SignalGuardPrototype/1.0 (MENA Ignite Hackathon dev build)"

lat_min = min(ENTRY[0], EXIT[0]) - LAT_MARGIN_DEG
lat_max = max(ENTRY[0], EXIT[0]) + LAT_MARGIN_DEG
lon_min = min(ENTRY[1], EXIT[1]) - LON_MARGIN_DEG
lon_max = max(ENTRY[1], EXIT[1]) + LON_MARGIN_DEG


def deg2num(lat, lon, zoom):
    lat_rad = math.radians(lat)
    n = 2.0**zoom
    xtile = (lon + 180.0) / 360.0 * n
    ytile = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return xtile, ytile


def num2deg(xtile, ytile, zoom):
    n = 2.0**zoom
    lon = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat = math.degrees(lat_rad)
    return lat, lon


x0f, y0f = deg2num(lat_max, lon_min, ZOOM)  # top-left (max lat, min lon)
x1f, y1f = deg2num(lat_min, lon_max, ZOOM)  # bottom-right

x0, x1 = math.floor(x0f), math.ceil(x1f)
y0, y1 = math.floor(y0f), math.ceil(y1f)

cols = x1 - x0
rows = y1 - y0
print(f"zoom={ZOOM} grid={cols}x{rows} tiles={cols*rows}")

out = Image.new("RGB", (cols * TILE, rows * TILE), (20, 26, 25))

for row, ty in enumerate(range(y0, y1)):
    for col, tx in enumerate(range(x0, x1)):
        url = f"https://tile.openstreetmap.org/{ZOOM}/{tx}/{ty}.png"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = resp.read()
                break
            except Exception as exc:
                print(f"  retry {tx},{ty}: {exc}")
                time.sleep(1)
        else:
            raise SystemExit(f"failed to fetch tile {tx},{ty}")
        tile_path = Path(tempfile.gettempdir()) / f"sg_tile_{tx}_{ty}.png"
        tile_path.write_bytes(data)
        img = Image.open(tile_path).convert("RGB")
        out.paste(img, (col * TILE, row * TILE))
        tile_path.unlink()
        time.sleep(0.3)  # be polite to the tile server

# Exact geographic bounds of the stitched image (tile-aligned, not our
# original bbox request — the image is the full tile grid).
north, west = num2deg(x0, y0, ZOOM)
south, east = num2deg(x1, y1, ZOOM)

assets_dir = Path(__file__).parent.parent / "assets" / "map"
assets_dir.mkdir(parents=True, exist_ok=True)
out_path = assets_dir / "corridor.png"
out.save(out_path, optimize=True)
print(f"saved {out_path} ({out.size[0]}x{out.size[1]}, {out_path.stat().st_size} bytes)")

import json

meta = {
    "north": north, "south": south, "east": east, "west": west,
    "width_px": out.size[0], "height_px": out.size[1],
    "zoom": ZOOM,
    "attribution": "© OpenStreetMap contributors",
}
meta_path = assets_dir / "corridor.json"
meta_path.write_text(json.dumps(meta, indent=2))
print(f"saved {meta_path}")
print(meta)
