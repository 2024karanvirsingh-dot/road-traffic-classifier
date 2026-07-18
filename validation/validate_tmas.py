#!/usr/bin/env python3
"""Validate the classifier against FHWA TMAS continuous traffic counts.

TMAS (the FHWA Travel Monitoring Analysis System) is the one nationwide, free,
uniformly formatted source of measured traffic volumes in the USA. It plays no
part in the classifier itself, which uses only globally available OSM data;
this module only GRADES the classifier where measurements exist. US only by
nature, and clearly labeled as such.

Pipeline:
  1. Download the 2023 TMAS station file and all 12 monthly volume files
     (cached under validation/tmas/, about 320 MB, deleted rows kept as a
     small derived CSV so reruns are offline).
  2. Compute AADT per station: hourly volumes -> daily totals per direction
     and lane -> summed per station per day -> mean over every recorded day.
  3. Classify the Greater Boston window with the standard classifier.
  4. Match each station to the nearest classified segment within 150 m.
  5. Bin observed AADT (Low < 5,000; Moderate 5,000 to 20,000; High > 20,000
     vehicles/day) and report exact agreement, adjacent agreement, and the
     Spearman rank correlation between score and AADT.

Run:  python3 validation/validate_tmas.py
"""

import csv, io, json, os, sys, urllib.request, zipfile
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from classify import haversine_m, fetch_city, classify   # noqa: E402

TMAS_DIR = os.path.join(HERE, "tmas")
BASE = "https://www.fhwa.dot.gov/policyinformation/tables/tmasdata/2023"
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec"]
STATE = "25"                       # Massachusetts FIPS
BBOX = (42.28, -71.20, 42.46, -70.98)   # Greater Boston validation window
MATCH_RADIUS_M = 150
# Observed AADT bins (vehicles/day). Urban rule of thumb: local streets run
# under 5k, collectors and minor arterials 5k to 20k, major corridors above.
LOW_AADT, MODERATE_AADT = 5000, 20000
UA = "road-traffic-classifier/3.0 (validation)"


def download(url, dest):
    if os.path.exists(dest):
        return dest
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return dest


def load_stations():
    """Station id -> (lat, lon, description) for MA stations in the window."""
    path = download(f"{BASE}/2023_station_data.zip",
                    os.path.join(TMAS_DIR, "2023_station_data.zip"))
    out = {}
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.startswith("MA_"))
        for line in io.TextIOWrapper(z.open(name), encoding="latin-1"):
            p = line.split("|")
            if len(p) < 32 or p[0] != "S" or p[1] != STATE:
                continue
            try:
                lat = int(p[26].strip()) / 1e6
                lon = -int(p[27].strip()) / 1e6   # TMAS stores west positive
            except ValueError:
                continue
            if BBOX[0] <= lat <= BBOX[2] and BBOX[1] <= lon <= BBOX[3]:
                out[p[2].strip()] = (lat, lon, p[6].strip(),
                                     line.rstrip().split("|")[-1].strip())
    return out


# A station is matched only to segments of the facility type it instruments,
# from its published TMAS functional system: without this, a freeway station
# can snap to a frontage or side street that happens to sit a few meters closer.
FS_ALLOWED = {
    "1": {"motorway", "motorway_link", "trunk", "trunk_link"},      # interstate
    "2": {"motorway", "motorway_link", "trunk", "trunk_link"},      # other freeway
    "3": {"trunk", "primary", "secondary"},                          # principal arterial
    "4": {"primary", "secondary", "tertiary"},                       # minor arterial
    "5": {"secondary", "tertiary", "unclassified", "residential"},   # major collector
}


def station_aadt(station_ids):
    """AADT per station from 12 months of hourly volume records. Cached as CSV."""
    cache = os.path.join(HERE, "station_aadt_2023.csv")
    if os.path.exists(cache):
        return {r["station_id"]: (float(r["aadt"]), int(r["days"]))
                for r in csv.DictReader(open(cache))}
    daily = defaultdict(lambda: defaultdict(int))   # sid -> date -> vehicles
    for m in MONTHS:
        path = download(f"{BASE}/{m}_2023_ccs_data.zip",
                        os.path.join(TMAS_DIR, f"{m}_2023_ccs_data.zip"))
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not name.upper().startswith(("MA", "ma")):
                    continue
                for line in io.TextIOWrapper(z.open(name), encoding="latin-1"):
                    p = line.split("|")
                    # V|state|f_system|station_id|dir|lane|year|month|day|dow|hour_00..hour_23
                    if len(p) < 34 or p[0] != "V" or p[1] != STATE:
                        continue
                    sid = p[3].strip()
                    if sid not in station_ids:
                        continue
                    date = (p[7], p[8])
                    total = 0
                    for h in p[10:34]:
                        try:
                            total += int(h)
                        except ValueError:
                            pass
                    daily[sid][date] += total
        print(f"  {m}: cumulative stations with data {len(daily)}")
    result = {}
    with open(cache, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["station_id", "aadt", "days"])
        for sid, days in sorted(daily.items()):
            vals = [v for v in days.values() if v > 0]
            if len(vals) < 30:      # require a month of data to call it annual
                continue
            aadt = sum(vals) / len(vals)
            result[sid] = (aadt, len(vals))
            w.writerow([sid, round(aadt), len(vals)])
    return result


def observed_category(aadt):
    return "Low" if aadt < LOW_AADT else ("Moderate" if aadt <= MODERATE_AADT else "High")


def spearman(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def main():
    os.makedirs(TMAS_DIR, exist_ok=True)
    print("Loading TMAS stations in the Greater Boston window…")
    stations = load_stations()
    print(f"  {len(stations)} stations")
    print("Computing 2023 AADT from monthly hourly volumes…")
    aadt = station_aadt(set(stations))
    print(f"  {len(aadt)} stations with at least 30 recorded days")

    print("Classifying the Greater Boston window with the standard classifier…")
    raw = fetch_city(BBOX, os.path.join(TMAS_DIR, "greater_boston_raw.json"))
    segments, _ = classify(raw)

    rows, matched = [], 0
    for sid, (lat, lon, fs, desc) in sorted(stations.items()):
        if sid not in aadt:
            continue
        allowed = FS_ALLOWED.get(fs[:1])
        best, best_d = None, MATCH_RADIUS_M + 1
        for s in segments:
            if allowed and s["class"] not in allowed:
                continue
            for pt in s["geometry"]:
                d = haversine_m([lat, lon], pt)
                if d < best_d:
                    best, best_d = s, d
        if best is None:
            continue
        matched += 1
        a, days = aadt[sid]
        obs = observed_category(a)
        rows.append({
            "station_id": sid,
            "description": "".join(c if c.isascii() else " " for c in desc)[:60],
            "count_year": 2023, "days_recorded": days,
            "measured_aadt": round(a),
            "matched_road": best["name"], "osm_class": best["class"],
            "distance_m": round(best_d),
            "classifier_score": best["score"],
            "classifier_category": best["category"],
            "observed_category": obs,
            "agreement": "exact" if best["category"] == obs else
                         ("adjacent" if {best["category"], obs} != {"Low", "High"} else "off"),
        })

    with open(os.path.join(HERE, "results_tmas.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    exact = sum(1 for r in rows if r["agreement"] == "exact")
    adj = sum(1 for r in rows if r["agreement"] in ("exact", "adjacent"))
    rho = spearman([r["classifier_score"] for r in rows],
                   [r["measured_aadt"] for r in rows])
    summary = {
        "source": "FHWA TMAS 2023 continuous count stations",
        "window": "Greater Boston, bbox " + str(list(BBOX)),
        "aadt_bins": {"low_under": LOW_AADT, "moderate_to": MODERATE_AADT},
        "stations_matched": matched,
        "exact_agreement": f"{exact}/{matched}",
        "adjacent_or_better": f"{adj}/{matched}",
        "spearman_score_vs_aadt": round(rho, 3),
        "caveat": ("TMAS continuous stations sit almost entirely on freeways, so this "
                   "validates the High end of the scale and rank ordering, not the "
                   "Low/Moderate boundary."),
    }
    json.dump(summary, open(os.path.join(HERE, "summary_tmas.json"), "w"), indent=2)
    print(json.dumps(summary, indent=2))
    print("\nPer station results: validation/results_tmas.csv")


if __name__ == "__main__":
    main()
