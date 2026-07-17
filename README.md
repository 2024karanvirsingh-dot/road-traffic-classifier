# Road Traffic Classification from Public Map Data

Assignment submission by Karanvir Singh.

A lightweight, fully transparent algorithm that classifies roads as Low, Moderate, or High traffic using only free public data. It was applied to every named road in a Cambridge / Allston, MA study area (1,783 OSM road segments, 667 named roads) and runs end to end in under a second on a laptop. A 20 road sample table is in `results.md`, the full output is in `results.csv`, and `index.html` is an interactive map of the whole study area.

## 1. Data sources (all public, all free)

| Source | What it provides | Access |
|---|---|---|
| OpenStreetMap via the Overpass API | Road class (`highway` tag), lane counts, speed limits, one way flags, geometry | Free HTTP API, no key |
| OSM bus route relations | Which roads carry transit service (MBTA routes are mapped as relations) | Same Overpass query |
| OSM land use and amenity polygons | Commercial, retail, industrial areas plus universities, hospitals, schools (trip generators) | Same Overpass query |
| Published traffic counts (MassDOT Transportation Data Management System, or any state DOT equivalent) | Ground truth AADT for spot checking thresholds | Free download, used for calibration only, not required at runtime |

Everything the classifier needs at runtime comes from three cached Overpass queries, so the method works anywhere OSM has coverage, which is essentially everywhere.

## 2. Features

Per road segment:

1. **Functional road class** (`highway=motorway/trunk/primary/secondary/tertiary/residential/...`). The strongest single predictor: road class encodes the network role the road was built and signed for.
2. **Lane count** (`lanes`). Capacity that exists because demand justified it.
3. **Speed limit** (`maxspeed`, normalized to mph). Higher design speed correlates with arterial function.
4. **Transit overlap**: is the segment a member of any bus route relation. Transit agencies route buses along corridors with proven demand, so this is a free demand signal.
5. **Commercial proximity**: is the segment within about 150 m of commercial, retail, or industrial land use or a major trip generator (university, hospital, school). Computed with a coarse spatial grid rather than exact distances, which keeps it O(n).

## 3. Scoring algorithm

A plain additive score. No ML, every point is explainable.

```
score = class_points                  (motorway 40 ... residential 5)
      + 4  * (lanes - 1)
      + 0.4 * max(0, speed_mph - 25)
      + 6 if on a bus route
      + 5 if commercial land use nearby

category: score <= 20 -> Low, <= 38 -> Moderate, else High
```

Named roads are aggregated from their segments (mean score, max lanes and speed, any() for the boolean features). Thresholds were sanity checked against known Cambridge corridors: Memorial Drive, McGrath Highway, and Soldiers Field Road land in High, neighborhood streets land in Low, which matches published MassDOT count patterns for these corridors.

Why this design, per the brief's priorities:

* **Speed**: pure arithmetic over tags, roughly 2,000 segments per run in well under a second. A whole state is minutes.
* **Simplicity**: five features, one formula, two thresholds. Anyone can audit why a road got its label.
* **Scalability**: the only spatial operation is a grid hash lookup, so cost is linear in segment count. Swap the bounding box and it runs on any city.

## 4. Missing data handling

OSM tagging is incomplete (in this study area lanes and maxspeed are missing on most residential streets). The rule is: impute from road class, and say so.

* Missing `lanes` or `maxspeed` are filled with class based defaults (e.g. residential defaults to 1 lane, 25 mph; primary to 2 lanes, 30 mph).
* Every output row carries a **confidence** flag: `high` when both tags were observed, `medium` when one was imputed, `low` when both were. Imputation is never silent.
* Roads missing a `name` are still scored and mapped, they are just excluded from the named roads table.
* If the transit or land use layers are unavailable for a region, those terms contribute zero and the class based core still produces a usable ranking.

## 5. Sample results

20 roads spanning all three categories are in `results.md`; the full 667 road output is `results.csv`. Open `index.html` for the interactive map with per road score breakdowns.

## 6. Limitations and improvements

* **It measures designed capacity and demand proxies, not measured flow.** A road can be over built or a shortcut street can be rat run. Fix: calibrate the weights and thresholds by regressing against published AADT counts where they exist, then apply the fitted weights everywhere.
* **No time dimension.** The label is static; real traffic peaks. Improvement: add peak factors from land use mix (office heavy corridors peak on weekdays) or ingest GTFS headways so 10 buses per hour counts for more than 1 per hour.
* **OSM tag quality varies by region.** The confidence flag surfaces this, but in poorly mapped areas the classifier degrades toward class only. Improvement: fall back to satellite derived lane detection or centrality measures (betweenness on the road graph is a strong volume proxy and still free).
* **Thresholds are hand set.** They were sanity checked, not fitted. With even 30 to 50 count stations, the two cut points could be chosen to maximize agreement with binned AADT.
* **Aggregation by name is coarse.** Long roads change character (Massachusetts Avenue in Boston vs Lexington). Improvement: classify per segment (the map already does) and report named roads as a distribution rather than a single label.

## Files

* `classify.py` - fetch parsing, feature extraction, scoring, output generation
* `roads.json`, `bus_members.json`, `context.json` - cached Overpass responses
* `results.csv` / `results.md` - full and sample outputs
* `index.html` + `map_data.js` - interactive map

To reproduce: `python3 classify.py` (regenerates all outputs from the cached data). The Overpass queries used to build the caches are documented at the top of `classify.py`.
