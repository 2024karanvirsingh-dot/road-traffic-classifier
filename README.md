# Global Road Traffic Classification from Public Map Data

Assignment submission by Karanvir Singh.

A lightweight, fully transparent algorithm that classifies roads as Low, Moderate, or High traffic using only free public data, and runs on **any city on Earth by name**:

```
python3 classify.py "Nairobi, Kenya"
python3 classify.py "Tokyo, Japan"
python3 classify.py --bbox 42.360,-71.130,42.390,-71.090 --name "Cambridge, USA"
```

It has been applied to eight cities on five continents (Cambridge USA, London, Paris, Tokyo, Nairobi, Sao Paulo, Mumbai, Sydney), about 84,000 road segments in total. Classification itself takes about a tenth of a second per city; the one time data fetch is a few seconds. `index.html` is an interactive world map with a city picker. Per city outputs live under `data/<city>/`: `results.csv` (full), `results.md` (20 road sample table), `map.json` (map data).

## 1. Data sources (all public, all free, no API keys)

| Source | What it provides |
|---|---|
| OpenStreetMap via the Overpass API (with mirror fallback) | Road class, lanes, speed limits, one way flags, surface, geometry |
| OSM transit route relations (bus, trolleybus, tram, share taxi) | Which roads carry transit service, a proven demand signal that works from Boston's MBTA to Nairobi's matatus |
| OSM land use and amenity polygons | Commercial, retail, industrial areas plus universities, hospitals, schools, marketplaces (trip generators) |
| Nominatim geocoder | City name to bounding box |
| Published traffic counts (any DOT's AADT data) | Ground truth for calibrating thresholds; used for spot checks only, not required at runtime |

One Overpass request per city fetches everything and is cached, so reruns are fully offline.

## 2. Features (8 per segment)

1. **Functional road class** (`highway=*`), the strongest single predictor: class encodes the network role the road was built and signed for. Link roads (ramps) are scored explicitly.
2. **Lane count**: capacity that exists because demand justified it.
3. **Speed limit**, normalized to km/h from any tagging style (`50`, `30 mph`, `walk`, multi values).
4. **One way pairing**: one way primary/secondary/tertiary streets are usually halves of an arterial couplet.
5. **Transit overlap**: membership in any bus/trolleybus/tram/share taxi route relation.
6. **Commercial proximity**: within about 150 m of commercial/retail/industrial land use or a major trip generator, computed with a coarse spatial grid, O(n).
7. **Junction connectivity**: an endpoint shared by 4+ road segments marks an intersection hub, a free proxy for network centrality.
8. **Surface**: unpaved roads suppress through traffic (matters greatly outside North America and Europe).

## 3. Scoring algorithm

A plain additive score. No ML, every point is explainable.

```
score = class_points                (motorway 40 ... residential 5, links in between)
      + 4    * (lanes - 1)
      + 0.25 * max(0, kmh - 40)
      + 2 if one way arterial        + 6 if on a transit route
      + 5 if commercial nearby       + 3 if junction hub
      - 6 if unpaved

category: score <= 20 -> Low, <= 38 -> Moderate, else High
```

Named roads aggregate their segments (mean score, max lanes/speed, any() for booleans). Thresholds were sanity checked against known corridors: in Cambridge, Memorial Drive, McGrath Highway, and Soldiers Field Road land in High and neighborhood streets in Low, matching published MassDOT count patterns.

Per the brief's priorities:

* **Speed**: pure arithmetic over tags; about 2,000 segments classify in ~0.1 s. A whole country is minutes.
* **Simplicity**: eight features, one formula, two thresholds. Anyone can audit why a road got its label.
* **Scalability**: the only spatial operations are hash lookups (grid cells, endpoints), so cost is linear in segment count. The same code and weights ran unchanged on five continents.

## 4. Missing data handling

OSM tagging completeness varies enormously by region. The rule is: impute from road class, and say so.

* Missing `lanes` or `maxspeed` fill from class based defaults (e.g. residential: 1 lane, 30 km/h; primary: 2 lanes, 50 km/h).
* Every output row carries a **confidence** flag: `high` when both tags were observed, `medium` when one was imputed, `low` when both were. Imputation is never silent, and the flag doubles as a per city map coverage indicator.
* Unnamed roads are still scored and mapped, just excluded from the named road tables.
* If transit or land use layers are empty for a region, those terms contribute zero and the class based core still ranks sensibly.
* Overpass mirror fallback and response caching make the fetch robust and repeatable.

## 5. Sample results

Each `data/<city>/results.md` holds a 20 road sample spanning all three categories; `results.csv` holds every named road. Open `index.html` (serve the folder with any static server) and switch cities from the dropdown.

## 6. Limitations and improvements

* **It measures designed capacity and demand proxies, not measured flow.** Fix: regress the weights against published AADT counts where available, then apply the fitted weights globally.
* **No time dimension.** Improvement: ingest GTFS headways so 10 buses per hour counts for more than 1, and derive peak factors from land use mix.
* **Tag quality varies by region.** The confidence flag surfaces this; in sparsely tagged areas the classifier degrades toward class only. Improvement: betweenness centrality on the road graph (still free) or satellite derived lane counts.
* **Thresholds are hand set**, sanity checked rather than fitted. Even 30 to 50 count stations per region would let the two cut points be chosen to maximize agreement with binned AADT.
* **Name aggregation is coarse** for long roads that change character. The map already classifies per segment; tables could report distributions instead of a single label.

## Files

* `classify.py` - geocoding, single request Overpass fetch with caching and mirror fallback, feature extraction, scoring, all outputs
* `data/<city>/` - cached raw data plus results.csv, results.md, map.json per city
* `data/cities.js` - manifest for the map UI
* `index.html` - interactive multi city map

To reproduce a city: `python3 classify.py "City, Country"`. Everything regenerates from one command.
