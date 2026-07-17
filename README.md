# Road Traffic Classification from Public Map Data

Assignment submission by Karanvir Singh.

A lightweight, fully transparent algorithm that classifies roads as Low, Moderate, or High traffic using only free public data. The classifications represent estimated relative traffic intensity rather than measured vehicles per day: "High Traffic" means a road has characteristics commonly associated with higher traffic within the study area, it does not guarantee a particular count value.

## 1. Objective and selected study area

**Selected study area: Cambridge, Massachusetts** and immediately adjacent roads within the bounding box 42.360, -71.130 to 42.390, -71.090. Cambridge was selected because it contains residential streets, commercial corridors, transit routes, divided highways, and publicly documented traffic count locations, providing a useful mix of expected traffic conditions. The Cambridge outputs are in `data/cambridge-usa/`.

Because the method uses only globally available data, the identical code was additionally run on seven more cities on five continents (London, Paris, Tokyo, Nairobi, Sao Paulo, Mumbai, Sydney, about 84,000 segments in total) as a **scalability demonstration**, and the interactive map (`index.html`) includes a live search that classifies any city on Earth client side in about a second:

```
python3 classify.py "Nairobi, Kenya"
python3 classify.py --bbox 42.360,-71.130,42.390,-71.090 --name "Cambridge, USA"
```

Note: city name mode evaluates a fixed rectangular study window centered on the geocoded location (about 8 km tall by default, `--span` to change). It does not follow municipal boundaries.

## 2. Public data sources (all free, no API keys)

| Source | What it provides |
|---|---|
| OpenStreetMap via the Overpass API (with mirror fallback) | Road class, lanes, speed limits, one way flags, surface, geometry |
| OSM transit route relations (bus, trolleybus, tram, share taxi) | Which roads carry transit service, a proven demand signal that works from Boston's MBTA to Nairobi's matatus |
| OSM land use and amenity polygons | Commercial, retail, industrial areas plus universities, hospitals, schools, marketplaces (trip generators) |
| Nominatim geocoder | City name to coordinates |

Published traffic counts (for example MassDOT's Transportation Data Management System) are identified as the natural calibration source but are **not consumed by the algorithm** and no numeric comparison has been performed yet; see Validation below.

One Overpass request per city fetches everything and is cached under `data/<city>/raw.json`, so reruns are fully offline. Cambridge data were retrieved on 2026-07-17 (all eight cities on the same date). OSM changes over time, so a rerun without the cache may differ slightly.

## 3. Input features (8 per segment)

1. **Functional road class** (`highway=*`), the strongest single predictor: class encodes the network role the road was built and signed for. Link roads (ramps) are scored explicitly.
2. **Lane count**: capacity that exists because demand justified it. Values below 1 are treated as missing.
3. **Speed limit**, normalized to km/h from any tagging style (`50`, `30 mph`, `walk`, multi values).
4. **One way pairing**: one way primary/secondary/tertiary streets are usually halves of an arterial couplet.
5. **Transit overlap**: membership in any bus/trolleybus/tram/share taxi route relation.
6. **Commercial proximity**: a coarse local proximity indicator. Segment points are hashed into a grid of roughly 150 m cells and a segment counts as commercial adjacent if any of its sampled points shares a cell, or borders a cell, containing commercial/retail/industrial land use or a major trip generator. Depending on latitude and direction the effective reach is roughly 150 to 450 m. O(n), no exact distances.
7. **Junction connectivity**: an endpoint shared by 4 or more road segments marks an intersection hub, a free proxy for network centrality.
8. **Surface**: unpaved roads suppress through traffic (matters greatly outside North America and Europe).

## 4. Scoring algorithm

A plain additive score per segment. No ML, every point is explainable.

| Feature | Condition | Points |
|---|---|---|
| Road class: motorway | base | 40 |
| Road class: trunk | base | 34 |
| Road class: motorway link | base | 32 |
| Road class: primary | base | 28 |
| Road class: trunk link | base | 28 |
| Road class: primary link | base | 24 |
| Road class: secondary | base | 22 |
| Road class: secondary link | base | 19 |
| Road class: tertiary | base | 14 |
| Road class: unclassified | base | 8 |
| Road class: residential | base | 5 |
| Additional lanes | each lane above one | +4 |
| Speed above 40 km/h | each km/h | +0.25 |
| One way arterial (primary/secondary/tertiary) | yes | +2 |
| Transit route | yes | +6 |
| Commercial or trip generator nearby | yes | +5 |
| Junction hub | yes | +3 |
| Unpaved surface | yes | -6 |

Category thresholds:

```
score <= 20        Low Traffic
20 < score <= 38   Moderate Traffic
score > 38         High Traffic
```

**How the weights were selected.** The weights are heuristic rather than statistically fitted. Road class receives the largest weight because it represents the road's intended network role. Lane count and speed receive smaller capacity related adjustments. Transit, commercial activity, and junction connectivity act as demand proxies, and the unpaved penalty encodes suppressed through traffic. The thresholds were selected through manual inspection of Cambridge corridors and are intended to produce useful relative categories with minimal computation.

**Road level aggregation.** A named road's score is the mean of its segment scores, and every displayed feature is aggregated the same way: modal road class, mean lanes, mean speed, and the share of segments (0 to 1) that are one way, on transit, commercial adjacent, junction hubs, or unpaved. Applying the formula to a row's aggregate values therefore reproduces its score up to the small nonlinearity of the modal class and the speed hinge. The map classifies per segment, which is the more precise view for long roads that change character.

Per the brief's priorities:

* **Speed**: pure arithmetic over tags; about 2,000 segments classify in ~0.1 s. A whole country is minutes.
* **Simplicity**: eight features, one formula, two thresholds. Anyone can audit why a road got its label.
* **Scalability**: the only spatial operations are hash lookups (grid cells, endpoints), so cost is linear in segment count. The same code and weights ran unchanged on five continents.

## 5. Missing data handling

OSM tagging completeness varies enormously by region. The rule is: impute from road class, and say so.

* Missing, malformed, or nonpositive `lanes` and unparseable `maxspeed` fill from class based defaults (e.g. residential: 1 lane, 30 km/h; primary: 2 lanes, 50 km/h).
* Every road row carries a **confidence** flag (`high` both tags observed, `medium` one imputed, `low` both imputed) plus explicit `lanes_imputed_share` and `speed_imputed_share` columns. Imputation is never silent, and these double as a per city map coverage indicator.
* Unnamed roads are still scored and mapped, just excluded from the named road tables.
* If transit or land use layers are empty for a region, those terms contribute zero and the class based core still ranks sensibly.
* Overpass mirror fallback and response caching make the fetch robust and repeatable; an area with no supported roads raises a clear error instead of writing empty output.

## 6. Results

The primary deliverable is `data/cambridge-usa/`: `results.md` holds a 20 road sample spanning all three categories with every input feature, `results.csv` holds all 667 named roads. Each of the other seven cities has the same pair of files. Open `index.html` (any static server, or the hosted copy) to explore every scored segment interactively.

## 7. Validation and sanity checks

Numeric validation against published counts has **not** been performed yet; the checks so far are qualitative: in Cambridge, Memorial Drive, McGrath Highway, and Soldiers Field Road land in High and neighborhood streets land in Low, matching these corridors' known roles, and equivalent spot checks hold in the other cities (Uhuru Highway and the Nairobi Expressway top Nairobi, the Western Express Highway and the Sea Link top Mumbai). The first improvement listed below is to replace this with a real AADT comparison.

## 8. Limitations and improvements

* **It measures designed capacity and demand proxies, not measured flow.** Fix: regress the weights and thresholds against published AADT counts (MassDOT TDMS for the study area), report agreement, then apply the fitted weights globally.
* **No time dimension.** Improvement: ingest GTFS headways so 10 buses per hour counts for more than 1, and derive peak factors from land use mix.
* **Tag quality varies by region.** The confidence flag surfaces this; in sparsely tagged areas the classifier degrades toward class only. Improvement: betweenness centrality on the road graph (still free) or satellite derived lane counts.
* **Thresholds are hand set**, sanity checked rather than fitted.
* **Name aggregation blurs long roads** that change character; the per segment map view is the precise one.

## 9. Planet scale economics

The design goal was an algorithm cheap enough that "all the roads in the world" is a realistic target, and it is:

* Data: the full OSM planet file is a free ~80 GB download. Roughly 250 million road segments worldwide.
* Compute: about 10,000 segments per second per core in plain Python, so the planet is on the order of 7 core hours: overnight on a laptop, or well under $20 of rented compute. Reruns after OSM updates only need changed regions.
* Serving: pre rendered classifications for the whole world fit in a few GB of static vector tiles (PMTiles) on free tier object storage. No servers, no per request cost.

## 10. Code and reproduction

* `classify.py` - geocoding, single request Overpass fetch with caching and mirror fallback, feature extraction, scoring, all outputs
* `data/<city>/` - cached raw data plus results.csv, results.md, map.json per city
* `index.html` - interactive multi city map; also contains the same scoring formula ported to JavaScript for the live search, which doubles as proof of how simple the algorithm is

To reproduce a city: `python3 classify.py "City, Country"`. Everything regenerates from one command (offline if the cache exists).

## Data attribution

Map data © OpenStreetMap contributors, available under the Open Data Commons Open Database License (ODbL). OpenStreetMap data were accessed through the Overpass API; geocoding by Nominatim; basemap tiles by CARTO. Data retrieved 2026-07-17.
