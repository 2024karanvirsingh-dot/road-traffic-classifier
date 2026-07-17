#!/usr/bin/env python3
"""Road traffic classification from public OpenStreetMap data.

Classifies roads as Low / Moderate / High traffic using a transparent
additive score over OSM features. No ML, no paid data, no API keys.

Usage:
    python3 classify.py "Cambridge, Massachusetts"
    python3 classify.py "Nairobi" --span 0.05
    python3 classify.py --bbox 42.360,-71.130,42.390,-71.090 --name "Cambridge, MA"

The city name is geocoded with Nominatim, road + transit + land use data is
pulled in a single Overpass API request and cached under data/<slug>/, then
scored offline. Outputs per city:
    data/<slug>/results.csv        one row per named road: every feature, every
                                   score contribution, uncertainty fields
    data/<slug>/results.md         sample table (20 roads across all categories)
    data/<slug>/map.json           per segment scores for the interactive map
    data/<slug>/run_metadata.json  bbox, thresholds, version, retrieval date
and refreshes data/cities.js, the manifest the map UI reads.
"""

import argparse, csv, datetime, json, math, os, re, sys, time
import urllib.parse, urllib.request
from collections import defaultdict
from statistics import pstdev

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
UA = "road-traffic-classifier/3.0 (educational assignment demo)"
ALGORITHM_VERSION = "3.0.0"

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
DEFAULT_MPH = {   # class based speed defaults, mph
    "motorway": 55, "motorway_link": 35, "trunk": 45, "trunk_link": 30,
    "primary": 30, "primary_link": 25, "secondary": 30, "secondary_link": 25,
    "tertiary": 25, "unclassified": 25, "residential": 25,
}
UNPAVED = {"unpaved", "dirt", "gravel", "sand", "ground", "earth", "grass",
           "mud", "compacted", "fine_gravel", "pebblestone", "rock"}

# Trip generators contribute the MAXIMUM applicable value, not a sum, so a road
# beside a mall and a school gets 5, not 7. Keeps the term bounded and simple.
GENERATOR_POINTS = {
    "commercial": 5, "retail": 5, "marketplace": 4, "hospital": 4,
    "university": 4, "industrial": 3, "school": 2,
}

# Transit contributes by how many distinct routes use the segment: a corridor
# carrying four bus lines is a stronger demand signal than a single route.
# Service frequency (headways) is still not captured.
def transit_points(route_count):
    if route_count >= 4:
        return 7
    if route_count >= 2:
        return 5
    if route_count == 1:
        return 3
    return 0

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


def parse_speed_mph(tag):
    """Handles '30 mph', '50' (km/h per OSM convention), '50;70', 'walk'."""
    if not tag:
        return None
    t = tag.split(";")[0].strip().lower()
    if t == "walk":
        return 6.0
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(mph|knots)?$", t)
    if not m:
        return None
    v = float(m.group(1))
    if m.group(2) == "mph":
        return v
    if m.group(2) == "knots":
        return v * 1.15078
    return v * 0.621371   # bare numbers are km/h in OSM


def parse_lanes(tag):
    try:
        v = int(float(tag.split(";")[0]))
        return v if v >= 1 else None   # malformed 0/negative values fall back to class default
    except (AttributeError, ValueError):
        return None


def haversine_m(a, b):
    """Great circle distance in meters between two [lat, lon] points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(h))


def segment_length_m(geom):
    return sum(haversine_m(geom[i], geom[i + 1]) for i in range(len(geom) - 1))


def score_components(cls, lanes, mph, oneway, transit_routes, generator_pts,
                     junction, unpaved):
    """Returns the eight score contributions; the score is exactly their sum."""
    return {
        "class_pts": CLASS_POINTS.get(cls, 5),
        "lane_pts": 4 * (lanes - 1),          # capacity that exists because demand justified it
        "speed_pts": 0.4 * max(0, mph - 25),  # design speed above urban baseline
        "oneway_pts": 2 if oneway and cls in ("primary", "secondary", "tertiary") else 0,
        "transit_pts": transit_points(transit_routes),
        "commercial_pts": generator_pts,      # max applicable trip generator value
        "junction_pts": 3 if junction else 0, # segment feeds 3+ other roads: a connector
        "surface_pts": -6 if unpaved else 0,  # unpaved surface suppresses through traffic
    }


def score_segment(cls, lanes, mph, oneway, transit_routes, generator_pts,
                  junction, unpaved):
    return round(sum(score_components(cls, lanes, mph, oneway, transit_routes,
                                      generator_pts, junction, unpaved).values()), 1)


def categorize(score):
    return "Low" if score <= LOW_MAX else ("Moderate" if score <= MODERATE_MAX else "High")


def classification_margin(score):
    """Distance to the nearest category threshold: how stable the label is."""
    return round(min(abs(score - LOW_MAX), abs(score - MODERATE_MAX)), 1)


def margin_label(margin):
    return "borderline" if margin < 2 else ("moderate" if margin <= 5 else "stable")


COMPONENT_KEYS = ["class_pts", "lane_pts", "speed_pts", "oneway_pts",
                  "transit_pts", "commercial_pts", "junction_pts", "surface_pts"]


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

    route_count = defaultdict(int)   # way id -> number of distinct transit routes
    for rel in relations:
        for ref in set(m["ref"] for m in rel.get("members", []) if m["type"] == "way"):
            route_count[ref] += 1

    # Trip generator proximity via a coarse spatial grid (~150 m cells). Each
    # cell stores the highest value generator touching it; a segment picks up
    # the maximum over its own cell plus the eight neighbors. This is a coarse
    # local proximity indicator (reach roughly 150 to 450 m), not an exact buffer.
    CELL = 0.0015
    hot_cells = {}
    for e in landuse:
        tags = e.get("tags", {})
        kind = tags.get("landuse") or tags.get("amenity")
        pts = GENERATOR_POINTS.get(kind, 0)
        if not pts:
            continue
        for pt in e.get("geometry", []):
            key = (round(pt["lat"] / CELL), round(pt["lon"] / CELL))
            hot_cells[key] = max(hot_cells.get(key, 0), pts)

    def generator_points_near(geom):
        best = 0
        for pt in geom[:: max(1, len(geom) // 8)]:
            r0, c0 = round(pt["lat"] / CELL), round(pt["lon"] / CELL)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    best = max(best, hot_cells.get((r0 + dr, c0 + dc), 0))
        return best

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
        mph = parse_speed_mph(tags.get("maxspeed"))
        lanes_i, speed_i = lanes is None, mph is None
        if lanes is None:
            lanes = DEFAULT_LANES[cls]
        if mph is None:
            mph = DEFAULT_MPH[cls]
        oneway = tags.get("oneway") in ("yes", "-1")
        routes = route_count[w["id"]]
        gen_pts = generator_points_near(g)
        junction = max(end_count[endkey(g[0])], end_count[endkey(g[-1])]) >= 4
        unpaved = tags.get("surface") in UNPAVED
        comps = score_components(cls, lanes, mph, oneway, routes, gen_pts,
                                 junction, unpaved)
        score = round(sum(comps.values()), 1)
        conf = "high" if not (lanes_i or speed_i) else ("low" if lanes_i and speed_i else "medium")
        geometry = [[round(p["lat"], 5), round(p["lon"], 5)] for p in g]
        seg = {
            "name": tags.get("name", "(unnamed)"), "class": cls, "lanes": lanes,
            "mph": round(mph), "oneway": oneway, "transit_routes": routes,
            "generator_pts": gen_pts, "junction": junction, "unpaved": unpaved,
            "lanes_imputed": lanes_i, "speed_imputed": speed_i,
            "length_m": round(segment_length_m(geometry)),
            "components": comps,
            "score": score, "confidence": conf, "category": categorize(score),
            "geometry": geometry,
        }
        segments.append(seg)
        if seg["name"] != "(unnamed)":
            by_name[seg["name"]].append(seg)

    # Road level aggregation, LENGTH WEIGHTED so a 1 km segment counts 50x a
    # 20 m fragment. The road score is exactly the sum of the eight reported
    # mean contribution columns, so every row is auditable by addition.
    roads_out = []
    for name, segs in by_name.items():
        total_len = sum(s["length_m"] for s in segs) or 1
        wmean = lambda vals: sum(v * s["length_m"] for v, s in zip(vals, segs)) / total_len
        comps = {k: round(wmean([s["components"][k] for s in segs]), 2)
                 for k in COMPONENT_KEYS}
        score = round(sum(comps.values()), 1)
        scores = [s["score"] for s in segs]
        cats = [s["category"] for s in segs]
        dominant = max(set(cats), key=cats.count)
        lanes_imp = round(wmean([s["lanes_imputed"] for s in segs]), 2)
        speed_imp = round(wmean([s["speed_imputed"] for s in segs]), 2)
        margin = classification_margin(score)
        modal_cls = max(set(s["class"] for s in segs),
                        key=lambda c: sum(s["length_m"] for s in segs if s["class"] == c))
        row = {
            "road": name,
            "modal_class": modal_cls,
            "segments": len(segs),
            "length_m": round(total_len),
            "mean_lanes": round(wmean([s["lanes"] for s in segs]), 1),
            "mean_speed_mph": round(wmean([s["mph"] for s in segs])),
            "oneway_share": round(wmean([s["oneway"] for s in segs]), 2),
            "transit_share": round(wmean([s["transit_routes"] > 0 for s in segs]), 2),
            "max_transit_routes": max(s["transit_routes"] for s in segs),
            "commercial_share": round(wmean([s["generator_pts"] > 0 for s in segs]), 2),
            "junction_share": round(wmean([s["junction"] for s in segs]), 2),
            "unpaved_share": round(wmean([s["unpaved"] for s in segs]), 2),
            **comps,
            "score": score,
            "category": categorize(score),
            "min_segment_score": round(min(scores), 1),
            "max_segment_score": round(max(scores), 1),
            "score_std": round(pstdev(scores), 1) if len(scores) > 1 else 0.0,
            "dominant_category": dominant,
            "category_consistency": round(cats.count(dominant) / len(cats), 2),
            "classification_margin": margin,
            "margin_label": margin_label(margin),
            "lanes_imputed_share": lanes_imp,
            "speed_imputed_share": speed_imp,
            "data_completeness": round(1 - (lanes_imp + speed_imp) / 2, 2),
        }
        roads_out.append(row)
    roads_out.sort(key=lambda r: -r["score"])
    return segments, roads_out


def write_outputs(slug, city_name, center, bbox, segments, roads_out, retrieved_at):
    if not roads_out:
        raise RuntimeError("No named supported roads were found in the selected area.")
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
        f.write("Features and score contributions are length weighted means over the road's\n"
                "segments. The breakdown column lists the eight contributions (class + lanes\n"
                "+ speed + one way + transit + commercial + junction + surface); the score is\n"
                "exactly their sum. Consistency is the share of road length whose segments\n"
                "agree with the dominant category. Full columns are in results.csv.\n\n")
        f.write("| Road | Modal class | Mean lanes | Mean speed (mph) | Score breakdown | Score | Category | Consistency | Margin | Data completeness |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|\n")
        for r in picks:
            breakdown = " + ".join(str(r[k]) for k in COMPONENT_KEYS)
            f.write("| {road} | {modal_class} | {mean_lanes} | {mean_speed_mph} | {b} | {score} | {category} | {category_consistency} | {margin_label} | {data_completeness} |\n"
                    .format(b=breakdown, **r))

    json.dump({"city": city_name, "center": center, "segments": segments},
              open(os.path.join(d, "map.json"), "w"))

    json.dump({
        "algorithm_version": ALGORITHM_VERSION,
        "retrieved_at": retrieved_at,
        "study_area": city_name,
        "bbox_south_west_north_east": list(bbox),
        "thresholds": {"low_max": LOW_MAX, "moderate_max": MODERATE_MAX},
        "units": "mph, meters",
        "source": "OpenStreetMap via the Overpass API; geocoding by Nominatim",
        "license": "Map data (c) OpenStreetMap contributors, ODbL",
    }, open(os.path.join(d, "run_metadata.json"), "w"), indent=2)

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

    cache = os.path.join(DATA, slug, "raw.json")
    t0 = time.time()
    data = fetch_city(bbox, cache)
    retrieved_at = datetime.date.fromtimestamp(os.path.getmtime(cache)).isoformat()
    t1 = time.time()
    segments, roads_out = classify(data)
    write_outputs(slug, city_name, center, bbox, segments, roads_out, retrieved_at)
    cats = defaultdict(int)
    for r in roads_out:
        cats[r["category"]] += 1
    print(f"{city_name}: {len(segments)} segments -> {len(roads_out)} named roads "
          f"{dict(cats)}  fetch {t1-t0:.1f}s, classify {time.time()-t1:.2f}s")


if __name__ == "__main__":
    main()
