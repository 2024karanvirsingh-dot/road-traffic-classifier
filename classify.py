#!/usr/bin/env python3
"""Road traffic classification from public OpenStreetMap data.

Classifies every named road in a bounding box as Low / Moderate / High traffic
using a transparent additive score over OSM features. No ML, no paid data.

Inputs (fetched once via the Overpass API, cached as JSON):
  roads.json        ways tagged highway=* with tags + geometry
  bus_members.json  bus route relations (member way ids = transit overlap)
  context.json      commercial/retail/industrial landuse + big trip generators

Outputs:
  results.csv       one row per road (segments merged by name), features + score + category
  results.md        markdown table of the 20 sample roads for the writeup
  map_data.js       per-segment scores for the Leaflet demo map
"""

import json, csv, math, os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- scoring model
# Base points from OSM functional road class. This is the strongest single
# predictor of volume: road class encodes the network role planners built for.
CLASS_POINTS = {
    "motorway": 40, "motorway_link": 32, "trunk": 34, "primary": 28,
    "secondary": 22, "tertiary": 14, "unclassified": 8, "residential": 5,
}
# Class-based defaults used when a tag is missing (imputation is visible in output)
DEFAULT_LANES = {
    "motorway": 3, "motorway_link": 1, "trunk": 2, "primary": 2,
    "secondary": 2, "tertiary": 2, "unclassified": 1, "residential": 1,
}
DEFAULT_SPEED = {  # mph
    "motorway": 55, "motorway_link": 35, "trunk": 40, "primary": 30,
    "secondary": 30, "tertiary": 25, "unclassified": 25, "residential": 25,
}

LOW_MAX, MODERATE_MAX = 20, 38   # score thresholds


def parse_speed(tag):
    if not tag:
        return None
    t = tag.split(";")[0].strip()
    try:
        if "mph" in t:
            return float(t.replace("mph", "").strip())
        return float(t) * 0.621371  # km/h -> mph
    except ValueError:
        return None


def parse_lanes(tag):
    try:
        return int(float(tag.split(";")[0]))
    except (AttributeError, ValueError):
        return None


def score_road(cls, lanes, speed, bus, near_commercial, lanes_imputed, speed_imputed):
    s = CLASS_POINTS.get(cls, 5)
    s += 4 * (lanes - 1)              # each extra lane adds capacity actually used
    s += max(0, (speed - 25)) * 0.4   # design speed above neighborhood baseline
    if bus:
        s += 6                        # transit agencies route buses where demand is
    if near_commercial:
        s += 5                        # retail/commercial frontage generates trips
    confidence = "high"
    if lanes_imputed and speed_imputed:
        confidence = "low"
    elif lanes_imputed or speed_imputed:
        confidence = "medium"
    return round(s, 1), confidence


def categorize(score):
    if score <= LOW_MAX:
        return "Low"
    if score <= MODERATE_MAX:
        return "Moderate"
    return "High"


def main():
    roads = json.load(open(os.path.join(HERE, "roads.json")))["elements"]
    bus = json.load(open(os.path.join(HERE, "bus_members.json")))["elements"]
    ctx = json.load(open(os.path.join(HERE, "context.json")))["elements"]

    bus_ways = set()
    for rel in bus:
        for m in rel.get("members", []):
            if m["type"] == "way":
                bus_ways.add(m["ref"])

    # Commercial proximity via a coarse spatial grid (~150 m cells). Fast and
    # good enough: we only need "is there commercial land use nearby", not distance.
    CELL = 0.0015
    commercial_cells = set()
    for e in ctx:
        if e["type"] != "way" or "geometry" not in e:
            continue
        tags = e.get("tags", {})
        if tags.get("landuse") in ("commercial", "retail", "industrial") or \
           tags.get("amenity") in ("university", "hospital", "school"):
            for pt in e["geometry"]:
                commercial_cells.add((round(pt["lat"] / CELL), round(pt["lon"] / CELL)))

    def near_commercial(geom):
        for pt in geom[:: max(1, len(geom) // 8)]:
            c = (round(pt["lat"] / CELL), round(pt["lon"] / CELL))
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if (c[0] + dr, c[1] + dc) in commercial_cells:
                        return True
        return False

    segments = []           # every scored segment, for the map
    by_name = defaultdict(list)

    for w in roads:
        tags = w.get("tags", {})
        cls = tags.get("highway")
        if cls not in CLASS_POINTS:
            continue
        lanes = parse_lanes(tags.get("lanes"))
        speed = parse_speed(tags.get("maxspeed"))
        lanes_i, speed_i = lanes is None, speed is None
        if lanes is None:
            lanes = DEFAULT_LANES[cls]
        if speed is None:
            speed = DEFAULT_SPEED[cls]
        on_bus = w["id"] in bus_ways
        comm = near_commercial(w.get("geometry", []))
        score, conf = score_road(cls, lanes, speed, on_bus, comm, lanes_i, speed_i)
        seg = {
            "id": w["id"], "name": tags.get("name", "(unnamed)"), "class": cls,
            "lanes": lanes, "speed_mph": round(speed), "bus_route": on_bus,
            "near_commercial": comm, "lanes_imputed": lanes_i,
            "speed_imputed": speed_i, "score": score, "confidence": conf,
            "category": categorize(score),
            "geometry": [[round(p["lat"], 5), round(p["lon"], 5)] for p in w.get("geometry", [])],
        }
        segments.append(seg)
        if seg["name"] != "(unnamed)":
            by_name[seg["name"]].append(seg)

    # Merge segments into whole roads: length-weighted-ish by segment count,
    # take max lanes/speed (a road is as busy as its busiest stretch tends to be)
    roads_out = []
    for name, segs in by_name.items():
        score = round(sum(s["score"] for s in segs) / len(segs), 1)
        rep_cls = max(segs, key=lambda s: CLASS_POINTS[s["class"]])["class"]
        row = {
            "road": name,
            "osm_class": rep_cls,
            "segments": len(segs),
            "lanes": max(s["lanes"] for s in segs),
            "speed_mph": max(s["speed_mph"] for s in segs),
            "bus_route": any(s["bus_route"] for s in segs),
            "near_commercial": any(s["near_commercial"] for s in segs),
            "score": score,
            "category": categorize(score),
            "confidence": min((s["confidence"] for s in segs),
                              key=["low", "medium", "high"].index),
        }
        roads_out.append(row)
    roads_out.sort(key=lambda r: -r["score"])

    with open(os.path.join(HERE, "results.csv"), "w", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=list(roads_out[0].keys()))
        wtr.writeheader()
        wtr.writerows(roads_out)

    # 20-road sample for the writeup: spread across categories, well-known names
    sample = [r for r in roads_out if r["segments"] >= 2]
    high = [r for r in sample if r["category"] == "High"][:7]
    mod = [r for r in sample if r["category"] == "Moderate"][:7]
    low = [r for r in sample if r["category"] == "Low"][:6]
    picks = high + mod + low
    with open(os.path.join(HERE, "results.md"), "w") as f:
        f.write("| Road | OSM class | Lanes | Speed (mph) | Bus route | Commercial nearby | Score | Category | Confidence |\n")
        f.write("|---|---|---|---|---|---|---|---|---|\n")
        for r in picks:
            f.write("| {road} | {osm_class} | {lanes} | {speed_mph} | {b} | {c} | {score} | {category} | {confidence} |\n".format(
                b="yes" if r["bus_route"] else "no",
                c="yes" if r["near_commercial"] else "no", **r))

    with open(os.path.join(HERE, "map_data.js"), "w") as f:
        f.write("const ROADS = ")
        json.dump([{k: s[k] for k in ("name", "class", "lanes", "speed_mph",
                                       "bus_route", "near_commercial", "score",
                                       "category", "confidence", "geometry")}
                   for s in segments], f)
        f.write(";\n")

    cats = defaultdict(int)
    for r in roads_out:
        cats[r["category"]] += 1
    print(f"{len(segments)} segments -> {len(roads_out)} named roads")
    print(dict(cats))
    print("sample table:", len(picks), "roads")


if __name__ == "__main__":
    main()
