#!/usr/bin/env python3
"""Global road traffic classification from public OpenStreetMap data.

Classifies every road in any city on Earth as Low / Moderate / High traffic
using a transparent additive score over OSM features. No ML, no paid data,
no API keys.

Usage:
    python3 classify.py "Cambridge, Massachusetts"
    python3 classify.py "Nairobi" --span 0.05
    python3 classify.py --bbox 42.360,-71.130,42.390,-71.090 --name "Cambridge, MA"

The city name is geocoded with Nominatim, road + transit + land use data is
pulled in a single Overpass API request and cached under data/<slug>/, then
scored offline. Outputs per city:
    data/<slug>/results.csv   one row per named road: features, score, category
    data/<slug>/results.md    sample table (20 roads across all categories)
    data/<slug>/map.json      per segment scores for the interactive map
and refreshes data/cities.js, the manifest the map UI reads.
"""

import argparse, csv, json, os, re, sys, time, urllib.parse, urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
UA = "road-traffic-classifier/2.0 (educational assignment demo)"

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# ---------------------------------------------------------------- scoring model
# Base points from OSM functional road class, the strongest single predictor
# of volume: class encodes the network role the road was built and signed for.
CLASS_POINTS = {
    "motorway": 40, "motorway_link": 32, "trunk": 34, "trunk_link": 28,
    "primary": 28, "primary_link": 24, "secondary": 22, "secondary_link": 19,
    "tertiary": 14, "unclassified": 8, "residential": 5,
}
DEFAULT_LANES = {
    "motorway": 3, "motorway_link": 1, "trunk": 2, "trunk_link": 1,
    "primary": 2, "primary_link": 1, "secondary": 2, "secondary_link": 1,
    "tertiary": 2, "unclassified": 1, "residential": 1,
}
DEFAULT_KMH = {   # class based speed defaults, km/h (world standard unit)
    "motorway": 90, "motorway_link": 60, "trunk": 70, "trunk_link": 50,
    "primary": 50, "primary_link": 40, "secondary": 50, "secondary_link": 40,
    "tertiary": 40, "unclassified": 40, "residential": 30,
}
UNPAVED = {"unpaved", "dirt", "gravel", "sand", "ground", "earth", "grass",
           "mud", "compacted", "fine_gravel", "pebblestone", "rock"}

LOW_MAX, MODERATE_MAX = 20, 38


def http_get(url, data=None, timeout=180):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def geocode(place):
    q = urllib.parse.urlencode({"q": place, "format": "json", "limit": 1})
    res = json.loads(http_get("https://nominatim.openstreetmap.org/search?" + q))
    if not res:
        sys.exit(f"Could not geocode '{place}'")
    hit = res[0]
    return float(hit["lat"]), float(hit["lon"]), hit["display_name"]


def fetch_city(bbox, cache_path):
    """One Overpass request per city: roads with geometry, transit route
    relations, and trip generating land use. Cached so reruns are offline."""
    if os.path.exists(cache_path):
        return json.load(open(cache_path))
    classes = "|".join(k for k in CLASS_POINTS)
    b = ",".join(f"{v:.4f}" for v in bbox)
    query = f"""[out:json][timeout:150];
way["highway"~"^({classes})$"]({b});out tags geom;
relation["route"~"^(bus|trolleybus|tram|share_taxi)$"]({b});out;
(way["landuse"~"^(commercial|retail|industrial)$"]({b});
 way["amenity"~"^(university|hospital|school|marketplace)$"]({b}););out tags geom;
"""
    last_err = None
    for mirror in OVERPASS_MIRRORS:
        try:
            raw = http_get(mirror, data=urllib.parse.urlencode({"data": query}).encode())
            data = json.loads(raw)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            json.dump(data, open(cache_path, "w"))
            return data
        except Exception as e:  # rate limit or mirror down: try the next one
            last_err = e
            time.sleep(5)
    sys.exit(f"Overpass fetch failed on all mirrors: {last_err}")


def parse_speed_kmh(tag):
    """Handles '50', '30 mph', '50;70', 'walk', 'RU:urban' style values."""
    if not tag:
        return None
    t = tag.split(";")[0].strip().lower()
    if t == "walk":
        return 10.0
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(mph|knots)?$", t)
    if not m:
        return None
    v = float(m.group(1))
    if m.group(2) == "mph":
        v *= 1.609344
    elif m.group(2) == "knots":
        v *= 1.852
    return v


def parse_lanes(tag):
    try:
        return int(float(tag.split(";")[0]))
    except (AttributeError, ValueError):
        return None


def score_segment(cls, lanes, kmh, oneway, transit, commercial, junction, unpaved):
    s = CLASS_POINTS.get(cls, 5)
    s += 4 * (lanes - 1)               # capacity that exists because demand justified it
    s += 0.25 * max(0, kmh - 40)       # design speed above urban baseline
    if oneway and cls in ("primary", "secondary", "tertiary"):
        s += 2                         # urban one way pairs are arterial couplets
    if transit:
        s += 6                         # agencies route transit where demand is proven
    if commercial:
        s += 5                         # retail and big institutions generate trips
    if junction:
        s += 3                         # segment feeds 3+ other roads: a connector
    if unpaved:
        s -= 6                         # unpaved surface suppresses through traffic
    return round(s, 1)


def categorize(score):
    return "Low" if score <= LOW_MAX else ("Moderate" if score <= MODERATE_MAX else "High")


def classify(data):
    elements = data["elements"]
    roads, relations, landuse = [], [], []
    for e in elements:
        if e["type"] == "relation":
            relations.append(e)
        elif e.get("tags", {}).get("highway") in CLASS_POINTS:
            roads.append(e)
        else:
            landuse.append(e)

    transit_ways = set()
    for rel in relations:
        for m in rel.get("members", []):
            if m["type"] == "way":
                transit_ways.add(m["ref"])

    # Commercial proximity via a coarse spatial grid (~150 m cells): we only
    # need "is there a trip generator nearby", not an exact distance. O(n).
    CELL = 0.0015
    hot_cells = set()
    for e in landuse:
        for pt in e.get("geometry", []):
            hot_cells.add((round(pt["lat"] / CELL), round(pt["lon"] / CELL)))

    def near_commercial(geom):
        for pt in geom[:: max(1, len(geom) // 8)]:
            r0, c0 = round(pt["lat"] / CELL), round(pt["lon"] / CELL)
            if any((r0 + dr, c0 + dc) in hot_cells for dr in (-1, 0, 1) for dc in (-1, 0, 1)):
                return True
        return False

    # Junction connectivity: hash segment endpoints; an endpoint shared by 4+
    # segments marks a real intersection hub. Free proxy for network centrality.
    def endkey(pt):
        return (round(pt["lat"], 5), round(pt["lon"], 5))
    end_count = defaultdict(int)
    for w in roads:
        g = w.get("geometry", [])
        if len(g) >= 2:
            end_count[endkey(g[0])] += 1
            end_count[endkey(g[-1])] += 1

    segments, by_name = [], defaultdict(list)
    for w in roads:
        tags, g = w.get("tags", {}), w.get("geometry", [])
        if len(g) < 2:
            continue
        cls = tags["highway"]
        lanes = parse_lanes(tags.get("lanes"))
        kmh = parse_speed_kmh(tags.get("maxspeed"))
        lanes_i, speed_i = lanes is None, kmh is None
        if lanes is None:
            lanes = DEFAULT_LANES[cls]
        if kmh is None:
            kmh = DEFAULT_KMH[cls]
        oneway = tags.get("oneway") in ("yes", "-1")
        transit = w["id"] in transit_ways
        commercial = near_commercial(g)
        junction = max(end_count[endkey(g[0])], end_count[endkey(g[-1])]) >= 4
        unpaved = tags.get("surface") in UNPAVED
        score = score_segment(cls, lanes, kmh, oneway, transit, commercial, junction, unpaved)
        conf = "high" if not (lanes_i or speed_i) else ("low" if lanes_i and speed_i else "medium")
        seg = {
            "name": tags.get("name", "(unnamed)"), "class": cls, "lanes": lanes,
            "kmh": round(kmh), "oneway": oneway, "transit": transit,
            "commercial": commercial, "junction": junction, "unpaved": unpaved,
            "score": score, "confidence": conf, "category": categorize(score),
            "geometry": [[round(p["lat"], 5), round(p["lon"], 5)] for p in g],
        }
        segments.append(seg)
        if seg["name"] != "(unnamed)":
            by_name[seg["name"]].append(seg)

    roads_out = []
    for name, segs in by_name.items():
        score = round(sum(s["score"] for s in segs) / len(segs), 1)
        roads_out.append({
            "road": name,
            "osm_class": max(segs, key=lambda s: CLASS_POINTS[s["class"]])["class"],
            "segments": len(segs),
            "lanes": max(s["lanes"] for s in segs),
            "speed_kmh": max(s["kmh"] for s in segs),
            "transit": any(s["transit"] for s in segs),
            "commercial": any(s["commercial"] for s in segs),
            "score": score,
            "category": categorize(score),
            "confidence": min((s["confidence"] for s in segs),
                              key=["low", "medium", "high"].index),
        })
    roads_out.sort(key=lambda r: -r["score"])
    return segments, roads_out


def write_outputs(slug, city_name, center, segments, roads_out):
    d = os.path.join(DATA, slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "results.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(roads_out[0].keys()))
        w.writeheader()
        w.writerows(roads_out)

    multi = [r for r in roads_out if r["segments"] >= 2] or roads_out
    picks = ([r for r in multi if r["category"] == "High"][:7]
             + [r for r in multi if r["category"] == "Moderate"][:7]
             + [r for r in multi if r["category"] == "Low"][:6])
    with open(os.path.join(d, "results.md"), "w") as f:
        f.write(f"### {city_name}\n\n")
        f.write("| Road | OSM class | Lanes | Speed (km/h) | Transit | Commercial | Score | Category | Confidence |\n")
        f.write("|---|---|---|---|---|---|---|---|---|\n")
        for r in picks:
            f.write("| {road} | {osm_class} | {lanes} | {speed_kmh} | {t} | {c} | {score} | {category} | {confidence} |\n"
                    .format(t="yes" if r["transit"] else "no",
                            c="yes" if r["commercial"] else "no", **r))

    json.dump({"city": city_name, "center": center, "segments": segments},
              open(os.path.join(d, "map.json"), "w"))

    # refresh the manifest the map UI reads
    cities = []
    for s in sorted(os.listdir(DATA)):
        mp = os.path.join(DATA, s, "map.json")
        if os.path.isfile(mp):
            m = json.load(open(mp))
            cities.append({"slug": s, "city": m["city"], "center": m["center"],
                           "segments": len(m["segments"])})
    with open(os.path.join(DATA, "cities.js"), "w") as f:
        f.write("const CITIES = ")
        json.dump(cities, f)
        f.write(";\n")


def main():
    ap = argparse.ArgumentParser(description="Classify road traffic for any city from OSM data")
    ap.add_argument("place", nargs="?", help="city name, e.g. 'Nairobi' or 'Paris, France'")
    ap.add_argument("--bbox", help="south,west,north,east (skips geocoding)")
    ap.add_argument("--name", help="display name when using --bbox")
    ap.add_argument("--span", type=float, default=0.04,
                    help="half size of the study box in degrees (default 0.04, about 4 km)")
    a = ap.parse_args()

    if a.bbox:
        bbox = tuple(float(x) for x in a.bbox.split(","))
        city_name = a.name or a.bbox
        center = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]
    elif a.place:
        lat, lon, display = geocode(a.place)
        city_name = display.split(",")[0] + ", " + display.split(",")[-1].strip()
        bbox = (lat - a.span, lon - a.span * 1.3, lat + a.span, lon + a.span * 1.3)
        center = [lat, lon]
    else:
        ap.error("give a place name or --bbox")

    # Slug from the user's input (ASCII), not the localized display name:
    # geocoding "Tokyo, Japan" returns a Japanese display name that would
    # otherwise slugify to an empty string.
    slug_src = a.place or a.name or a.bbox
    slug = re.sub(r"[^a-z0-9]+", "-", slug_src.lower().encode("ascii", "ignore").decode()).strip("-")
    if not slug:
        slug = "city"
    if a.place and city_name.encode("ascii", "ignore").decode().strip(", ") == "":
        city_name = a.place  # keep a readable name when the display name is non-Latin
    t0 = time.time()
    data = fetch_city(bbox, os.path.join(DATA, slug, "raw.json"))
    t1 = time.time()
    segments, roads_out = classify(data)
    write_outputs(slug, city_name, center, segments, roads_out)
    cats = defaultdict(int)
    for r in roads_out:
        cats[r["category"]] += 1
    print(f"{city_name}: {len(segments)} segments -> {len(roads_out)} named roads "
          f"{dict(cats)}  fetch {t1-t0:.1f}s, classify {time.time()-t1:.2f}s")


if __name__ == "__main__":
    main()
