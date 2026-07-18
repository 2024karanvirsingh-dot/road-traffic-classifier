# Road Traffic Classification from Public Map Data

Assignment submission by Karanvir Singh.

> This project classifies roads as Low, Moderate, or High traffic using only publicly available OpenStreetMap data that exists for any city on Earth. It uses eight explainable features and a simple additive score rather than machine learning, and every reported score is exactly the sum of eight printed contribution columns. Missing lane and speed values are imputed from road class and explicitly flagged. The method classified 667 named roads in the Cambridge, Massachusetts study area and was additionally run unchanged on seven international cities (about 84,000 segments) to demonstrate scalability, including a live in-browser mode that classifies any searched city in about a second.

The classifications represent estimated relative traffic intensity rather than measured vehicles per day: "High Traffic" means a road has characteristics commonly associated with higher traffic within the study area, it does not guarantee a particular count value.

## 1. Selected study area

**Cambridge, Massachusetts** and immediately adjacent roads within the bounding box 42.360, -71.130 to 42.390, -71.090. Cambridge was selected because it contains residential streets, commercial corridors, transit routes, and divided highways, providing a useful mix of expected traffic conditions. Outputs are in `data/cambridge-usa/`.

Because every input is globally available, the identical code also ran on London, Paris, Tokyo, Nairobi, Sao Paulo, Mumbai, and Sydney as a **scalability demonstration**, and the interactive map (`index.html`) includes a live search that classifies any city on Earth client side:

```
python3 classify.py "Nairobi, Kenya"
python3 classify.py --bbox 42.360,-71.130,42.390,-71.090 --name "Cambridge, USA"
```

City name mode evaluates a fixed rectangular window centered on the geocoded location (about 5.5 miles tall by default, `--span` to change). It does not follow municipal boundaries.

## 2. Public data sources

Only sources that are free, keyless, and available worldwide are used. Anything region specific (for example a state DOT count program) is deliberately excluded from the pipeline.

| Source | What it provides |
|---|---|
| OpenStreetMap via the Overpass API (with mirror fallback) | Road class, lanes, speed limits, one way flags, surface, geometry |
| OSM transit route relations (bus, trolleybus, tram, share taxi) | How many transit routes use each road, a proven demand signal that works from Boston's MBTA to Nairobi's matatus |
| OSM land use and amenity polygons | Commercial, retail, industrial areas plus universities, hospitals, schools, marketplaces (trip generators) |
| Nominatim geocoder | City name to coordinates |

One Overpass request per city fetches everything and is cached under `data/<city>/raw.json`, so reruns are fully offline. All eight cities were retrieved on 2026-07-17 (recorded per city in `run_metadata.json`). OSM changes over time, so a rerun without the cache may differ slightly.

**Validation only (never a runtime input):** FHWA TMAS, the Travel Monitoring Analysis System, is used to grade the classifier against measured traffic. It is free and covers the whole USA in one uniform format, but it is US only, so it lives in a separate `validation/` module and the classifier itself never reads it.

## 3. System pipeline

```
City name or bounding box
        |
Nominatim geocoding (name mode only)
        |
One Overpass request  ->  cached raw.json
        |
Road + transit + land use extraction
        |
Missing value imputation (flagged, never silent)
        |
Eight feature segment scoring (score = sum of 8 contributions)
        |
Length weighted road level aggregation + uncertainty fields
        |
results.csv + results.md + map.json + run_metadata.json + interactive map
```

## 4. Input features (8 per segment)

1. **Functional road class** (`highway=*`), the strongest single predictor: class encodes the network role the road was built and signed for. Link roads (ramps) are scored explicitly.
2. **Lane count**: capacity that exists because demand justified it. Values below 1 are treated as missing.
3. **Speed limit**, normalized to mph from any tagging style (`30 mph`, `50` meaning km/h per OSM convention, `walk`, multi values).
4. **One way pairing**: one way primary/secondary/tertiary streets are usually halves of an arterial couplet.
5. **Transit route count**: how many distinct bus/trolleybus/tram/share taxi routes use the segment. Four routes is a stronger demand signal than one. Service frequency is still not captured.
6. **Trip generator proximity**: a coarse local proximity indicator. Segment points are hashed into a grid of roughly 500 ft cells; a segment picks up the highest value generator in its own or a neighboring cell (effective reach roughly 500 to 1500 ft). Contributions do not stack: a road beside a mall and a school gets the mall's value, not the sum.
7. **Junction connectivity**: an endpoint shared by 4 or more road segments marks an intersection hub, a free proxy for network centrality.
8. **Surface**: unpaved roads suppress through traffic (matters greatly outside North America and Europe).

## 5. Exact scoring formula

A plain additive score per segment. No ML. The score is exactly the sum of the eight contributions below, and all eight are printed per road in the outputs.

| Component | Condition | Points |
|---|---|---|
| class_pts | motorway | 40 |
| | trunk | 34 |
| | motorway link | 32 |
| | primary / trunk link | 28 |
| | primary link | 24 |
| | secondary | 22 |
| | secondary link | 19 |
| | tertiary | 14 |
| | unclassified | 8 |
| | residential | 5 |
| lane_pts | each lane above one | +4 |
| speed_pts | each mph above 25 | +0.4 |
| oneway_pts | one way primary/secondary/tertiary | +2 |
| transit_pts | 1 route / 2 to 3 routes / 4 or more | +3 / +5 / +7 |
| commercial_pts | highest value nearby generator: commercial or retail +5, marketplace, hospital, university +4, industrial +3, school +2 | +2 to +5 |
| junction_pts | endpoint shared by 4+ segments | +3 |
| surface_pts | unpaved | -6 |

Category thresholds:

```
score <= 20        Low Traffic
20 < score <= 38   Moderate Traffic
score > 38         High Traffic
```

**How the weights were selected.** The weights are heuristic rather than statistically fitted. Road class receives the largest weight because it represents the road's intended network role. Lane count and speed receive smaller capacity related adjustments. Transit route count, trip generators, and junction connectivity act as demand proxies, and the unpaved penalty encodes suppressed through traffic. Thresholds were selected through manual inspection of Cambridge corridors and are intended to produce useful relative categories with minimal computation.

## 6. Missing data handling

OSM tagging completeness varies enormously by region. The rule is: impute from road class, and say so.

* Missing, malformed, or nonpositive `lanes` and unparseable `maxspeed` fill from class based defaults (e.g. residential: 1 lane, 25 mph; primary: 2 lanes, 30 mph).
* Every road row carries `lanes_imputed_share`, `speed_imputed_share`, and a combined `data_completeness` (1 means every lane and speed value was observed). Imputation is never silent.
* Unnamed roads are still scored and mapped, just excluded from the named road tables.
* If transit or land use layers are empty for a region, those terms contribute zero and the class based core still ranks sensibly.
* Overpass mirror fallback and response caching make the fetch robust; an area with no supported roads raises a clear error instead of writing empty output.

## 7. Aggregation and uncertainty

Road level rows are **length weighted**: a 1 km segment counts 50 times a 20 m fragment, for the score, every feature mean, and every share. The road score is exactly the sum of its eight length weighted mean contribution columns, so every row is auditable by addition.

Because a named road can change character along its length, each row also reports:

* `min_segment_score`, `max_segment_score`, `score_std`: the spread behind the mean
* `dominant_category` and `category_consistency`: "Memorial Drive is High with 100% of its length classified High" is more meaningful than an average alone
* `classification_margin` and `margin_label`: distance to the nearest threshold (under 2 points is labeled borderline, 2 to 5 moderate, over 5 stable), so a road scoring 38.1 is not presented as certain as one scoring 55
* `data_completeness`: how much of the lane and speed input was observed rather than imputed

The map classifies per segment, which is the precise view for long roads.

## 8. Results

The primary deliverable is `data/cambridge-usa/`: `results.md` holds a 20 road sample spanning all three categories with per component score breakdowns, `results.csv` holds all 667 named roads with every column. Each of the other seven cities has the same files. Open `index.html` (any static server, or the hosted copy) to explore every scored segment; popups show the full breakdown.

## 9. Validation

**Measured counts (FHWA TMAS, USA wide).** `validation/validate_tmas.py` downloads the 2023 TMAS station file and all twelve monthly hourly volume files, computes AADT per continuous count station (mean of every recorded day, requiring at least 30 days), classifies the Greater Boston window with the standard classifier, and matches each station to the nearest segment of the facility type the station instruments (within 150 m, using the station's published functional class so a freeway station cannot snap to a side street). Observed bins: Low under 5,000 vehicles/day, Moderate 5,000 to 20,000, High above 20,000.

Results for the 11 stations with a full year of 2023 data (`validation/results_tmas.csv`, summary in `validation/summary_tmas.json`):

| Metric | Result |
|---|---|
| Exact category agreement | 11 / 11 |
| Adjacent or better | 11 / 11 |
| Spearman rank correlation, score vs measured AADT | 0.547 |
| Measured AADT range | 41,526 to 149,226 vehicles/day |

Honest caveats: TMAS continuous stations in this window sit entirely on freeways and expressways, so this validates the High end of the scale, not the Low/Moderate boundary. And because all 11 matched roads are the same facility type, the classifier's features saturate there, which is why within freeway rank ordering is only moderate (0.547). The category level claim it does support is strong: every road that measured above 41,000 vehicles/day is classified High.

**Qualitative spot checks (global).** In Cambridge, Memorial Drive, McGrath Highway, and Soldiers Field Road land in High and neighborhood streets land in Low, matching these corridors' known roles; equivalent checks hold in the other cities (Uhuru Highway and the Nairobi Expressway top Nairobi, the Western Express Highway and the Sea Link top Mumbai). Anyone applying this in production should calibrate weights and thresholds against local counts; the contribution columns make that a simple regression, and the TMAS module is a template for doing it anywhere the USA is in scope.

## 10. Runtime and scalability

* About 2,000 segments classify in under a second in plain Python; the largest test city (London, 19,613 segments) classifies in a few seconds.
* The only spatial operations are hash lookups (grid cells, endpoints), so cost is linear in segment count.
* The same code and weights ran unchanged on five continents, and the formula is small enough that the JavaScript port in `index.html` classifies any searched city live in the browser.
* Illustrative engineering estimates, not measured results: extrapolating the measured throughput to OSM's roughly 250 million road segments worldwide suggests planet scale classification is on the order of single digit core hours, and pre rendered results would fit in a few GB of static vector tiles.

## 11. Limitations

| Limitation | Likely effect | Improvement |
|---|---|---|
| No measured traffic at runtime | Labels estimate likely volume, not counts | TMAS validation covers the US High end; calibrate against local counts elsewhere |
| Continuous count stations cluster on freeways | Low/Moderate boundary is unvalidated by measurement | Add state DOT short duration counts, which cover arterials and local streets |
| No time dimension | Cannot distinguish peak and off peak | Ingest GTFS headways, derive peak factors from land use mix |
| Uneven OSM completeness | Lower reliability in poorly tagged areas | data_completeness flags it; betweenness centrality as a tag free signal |
| Route count ignores frequency | A 10 minute headway counts like an hourly one | Use GTFS service frequency where published |
| Name aggregation blurs long roads | A road spanning quiet and busy stretches averages out | Consistency and min/max columns expose it; the map is per segment |
| Hand selected thresholds | Categories may not transfer perfectly between regions | Fit region specific thresholds from local data |

## 12. Reproduction

* `classify.py` - geocoding, single request Overpass fetch with caching and mirror fallback, feature extraction, scoring, all outputs. Standard library only (`requirements.txt` documents the absence of dependencies).
* `tests/` - 15 unit tests over parsing, scoring, thresholds, and geometry; run with `python -m unittest discover -s tests`. GitHub Actions runs them on every push.
* `data/<city>/` - cached raw data plus results.csv, results.md, map.json, run_metadata.json (algorithm version, bbox, thresholds, retrieval date) per city.
* `index.html` - interactive multi city map; contains the same scoring formula ported to JavaScript for the live search, which doubles as proof of how simple the algorithm is.

To reproduce a city: `python3 classify.py "City, Country"`. Everything regenerates from one command (offline if the cache exists).

## Data attribution

Map data © OpenStreetMap contributors, available under the Open Data Commons Open Database License (ODbL). OpenStreetMap data were accessed through the Overpass API; geocoding by Nominatim; basemap tiles by CARTO. Data retrieved 2026-07-17. Validation traffic counts from the FHWA Travel Monitoring Analysis System (TMAS), 2023 station and continuous count volume files, US DOT public data.
