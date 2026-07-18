# Road Traffic Classification from Public Map Data

**Assignment submission by Karanvir Singh.**

## Summary

This project classifies roads as Low, Moderate, or High traffic using nothing but publicly available OpenStreetMap data. There is no machine learning, no paid data, and no API key anywhere in the pipeline. Every road gets a score that is the exact sum of eight printed contribution columns, so any number in the output can be checked by hand with simple addition.

The primary study area is Cambridge, Massachusetts, where the method classified 667 named roads using speed limits, road classifications, lane counts, transit routes, and surrounding land use. The rankings were checked three ways: against the known roles of the area's corridors, against measured traffic volumes from the free nationwide US count program (FHWA TMAS, Section 8), and by an automated test suite that runs on every push. Nothing in the pipeline is US specific, and that is the point: because every input the classifier uses exists worldwide, the same code can classify any city on Earth. To demonstrate that, I ran it unchanged on seven more cities across five continents (about 84,000 road segments in total), and the interactive map in index.html includes a live search, so you can type in whichever city you want and watch it classify in about a second, right in the browser.

Keeping the system lightweight was a deliberate choice, not a shortcut. I did not wire in a stack of regional databases, count portals, or commercial feeds, even though several exist, because every added source means another format, another failure mode, and another region where the tool silently stops working. Instead the whole pipeline runs on a single free data source, uses only the Python standard library, and answers the assignment's questions with the smallest amount of machinery that does the job well.

One clarification up front, because it shapes everything else in this document: the categories represent estimated relative traffic intensity, not measured vehicle counts. When the algorithm labels a road High Traffic, it is saying that the road has the characteristics that busy roads tend to have, within the study area. It is not claiming a specific number of vehicles per day. The assignment asks for speed, simplicity, and scalability over perfect accuracy, and I took that seriously as a design constraint rather than as permission to be sloppy. The result is a small, transparent scoring model where every decision is visible and every output is auditable.

## 1. Choosing the study area

I selected Cambridge, Massachusetts, specifically the bounding box from 42.360, -71.130 to 42.390, -71.090. Cambridge is a good test case because it packs a lot of variety into a small area: quiet residential streets, dense commercial corridors like Massachusetts Avenue, heavy transit coverage, a river parkway system, and divided highways like Memorial Drive and McGrath Highway. If a classifier cannot separate Memorial Drive from a dead end street off Huron Avenue, it is not doing anything useful, so the area comes with built in sanity checks.

Because every input the algorithm uses is available worldwide, the tool is not limited to Cambridge, or to the US. It can classify any city on Earth. To demonstrate that rather than just assert it, I ran the identical code, with identical weights, on a handful of global cities: London, Paris, Tokyo, Nairobi, Sao Paulo, Mumbai, and Sydney. Each city's outputs live under data/<city>/ in the same format as the Cambridge results, and the interactive map's search box lets you classify whichever city you want on demand.

Running a new city is one command:

```
python3 classify.py "Nairobi, Kenya"
python3 classify.py --bbox 42.360,-71.130,42.390,-71.090 --name "Cambridge, USA"
```

City name mode geocodes the name and evaluates a fixed rectangular window centered on the result, about 5.5 miles tall by default, adjustable with --span. It does not follow municipal boundaries, which keeps the query simple and the runtime predictable.

## 2. Public data sources

The assignment asks for suitable public data sources, and my selection rule was strict: a source had to be free, keyless, and available for essentially any populated place on Earth. That rule deliberately excludes some genuinely useful data. State DOT traffic count programs, for example, publish real measured volumes, but they only exist in some jurisdictions, they publish in inconsistent formats, and building a pipeline around them would mean the algorithm works in Massachusetts and fails in Nairobi. This is also where the lightweight philosophy shows up most clearly: one data source, one network request per city, no database server, no ingestion jobs, nothing to install. I discuss below how regional count programs should be used for calibration and validation where they exist, but the core pipeline does not depend on them.

Everything the classifier consumes comes from the OpenStreetMap ecosystem:

| Source | What it provides |
|---|---|
| OpenStreetMap via the Overpass API, with mirror fallback | Road class, lane counts, speed limits, one way flags, surface type, full road geometry |
| OSM transit route relations (bus, trolleybus, tram, share taxi) | How many distinct transit routes use each road segment |
| OSM land use and amenity polygons | Commercial, retail, and industrial zones, plus universities, hospitals, schools, and marketplaces |
| Nominatim geocoder | Converts a city name into coordinates |

Two things are worth calling out about this list. First, the transit signal is route relations rather than stop locations, which means it works identically whether the operator is Boston's MBTA or Nairobi's matatu network, because in both cases mappers tag the routes the same way. Second, the entire fetch for a city is a single Overpass request, and the raw response is cached under data/<city>/raw.json, so every rerun after the first is fully offline and deterministic. All eight cities in this repository were retrieved on 2026-07-17, and the retrieval date is recorded per city in run_metadata.json, because OSM is a living dataset and a later fetch may differ.

One measured count source is used, but for validation only and never as a runtime input: FHWA TMAS, the Travel Monitoring Analysis System, the one free nationwide US count program. It grades the classifier in Section 8, lives in a separate validation/ module, and the classifier itself never reads it.

## 3. The features

Choosing features was the most interesting part of this assignment, because the honest question is not "what correlates with traffic" but "what correlates with traffic and is actually tagged in OSM in most of the world." Lane counts are informative but frequently missing. Speed limits are informative but tagged in three different unit conventions. I settled on eight features per road segment, chosen so that the most important ones are also the most reliably present.

The first and most important is the functional road class, the highway tag. This is the strongest single predictor of traffic volume available in OSM, and the reasoning is almost circular in a useful way: the class encodes the role the road was designed, built, and signed for. A motorway exists because engineers projected high volumes. A residential street exists to reach the houses on it. Mappers essentially never omit this tag, because it is what makes a road render on the map at all, so the backbone of the score is available everywhere. Link roads, meaning ramps and connectors, are scored explicitly rather than inheriting their parent class, since a ramp carries real but lesser traffic.

Lane count is second. Extra lanes are expensive, so they tend to exist where demand justified building them. I treat lanes as a capacity signal that reflects past demand rather than as a direct measurement. Values are parsed defensively: a lane tag of zero or a negative number is treated as missing rather than trusted.

Speed limit is third, normalized to miles per hour from every tagging style OSM allows. A bare number means kilometers per hour by OSM convention, "30 mph" means what it says, "walk" maps to walking pace, and multi valued tags like "50;70" take the first value. Higher design speeds usually mean the road was built to move volume. The score only rewards speed above a 25 mph urban baseline, so a quiet 25 mph street gains nothing.

The remaining five features are cheaper signals that each capture something the first three miss. One way pairing adds a small bonus for one way primary, secondary, and tertiary streets, because in practice those are usually one half of an arterial couplet, and treating each half as an ordinary street would undercount the corridor. Transit route count adds points based on how many distinct routes use the segment, with more routes worth more, because transit planners concentrate service where people already travel; a corridor carrying four bus lines is a much stronger demand signal than one carrying a single route. Trip generator proximity gives points when a segment runs near commercial or retail land use, a marketplace, a hospital, a university, an industrial zone, or a school, with the single highest value generator counting rather than a sum, so a road beside both a mall and a school gets the mall's five points, not seven. Junction connectivity adds a bonus when a segment endpoint is shared by four or more road segments, which marks a genuine intersection hub and serves as a free stand in for expensive network centrality computations. Finally, surface applies a penalty for unpaved roads, which matters little in Cambridge but matters enormously in Nairobi, where an unpaved tertiary road carries far less through traffic than its class alone would suggest.

The trip generator feature deserves one implementation note, because it is the only place the algorithm does anything spatial. Rather than computing true distance buffers, which would be the slowest operation in the pipeline by far, every generator polygon is hashed into a coarse grid of roughly 500 foot cells, each cell remembering the highest value generator that touches it. A road segment then samples a few of its points and takes the maximum over each point's own cell and the eight surrounding cells. The effective reach is roughly 500 to 1500 feet, and the operation is a handful of dictionary lookups per segment. It is a coarse proximity indicator, not an exact buffer, and I consider that exactly the right trade for an assignment that prioritizes speed and simplicity.

## 4. The scoring algorithm

The algorithm is a plain additive score. Each segment's score is the sum of eight contributions, one per feature, and both the segment level and road level outputs print all eight, so the arithmetic is checkable by anyone with the table in front of them.

| Component | Condition | Points |
|---|---|---|
| class_pts | motorway | 40 |
| | trunk | 34 |
| | motorway link | 32 |
| | primary, trunk link | 28 |
| | primary link | 24 |
| | secondary | 22 |
| | secondary link | 19 |
| | tertiary | 14 |
| | unclassified | 8 |
| | residential | 5 |
| lane_pts | each lane above one | +4 |
| speed_pts | each mph above 25 | +0.4 |
| oneway_pts | one way primary, secondary, or tertiary | +2 |
| transit_pts | 1 route, 2 to 3 routes, 4 or more routes | +3, +5, +7 |
| commercial_pts | highest value nearby generator: commercial or retail +5, marketplace, hospital, or university +4, industrial +3, school +2 | +2 to +5 |
| junction_pts | endpoint shared by 4 or more segments | +3 |
| surface_pts | unpaved surface | -6 |

Categories come from two fixed thresholds:

```
score <= 20        Low Traffic
20 < score <= 38   Moderate Traffic
score > 38         High Traffic
```

In pseudocode, the whole scoring step is:

```
for each road segment:
    score = class_points[highway_class]
          + 4 * (lanes - 1)
          + 0.4 * max(0, speed_mph - 25)
          + (2 if oneway and class in arterial_classes else 0)
          + transit_points(distinct_route_count)
          + max_nearby_generator_points
          + (3 if endpoint_shared_by_4_or_more_segments else 0)
          + (-6 if unpaved else 0)
    category = Low if score <= 20, Moderate if score <= 38, else High
```

I want to be straightforward about how the weights were chosen, because pretending otherwise would undermine the transparency the design is built on. They are heuristic, not statistically fitted. Road class carries the largest weight because it is the most reliable and most predictive input. Lanes and speed act as moderate capacity adjustments on top of it. Transit, trip generators, and junctions act as demand proxies worth a handful of points each, enough to separate a busy secondary street from a sleepy one, but never enough to promote a residential street into High on their own. The unpaved penalty is negative because an unpaved surface actively suppresses through traffic regardless of what the class claims. The thresholds at 20 and 38 came from manually inspecting Cambridge corridors and adjusting until the boundaries fell where local knowledge said they should. A weighted sum with hand set weights is the price of having no training data requirement, and the structure is deliberately regression shaped so that anyone with local count data can fit the weights properly later. Section 8 shows the first step of exactly that.

Why not machine learning? Three reasons. Training a model requires labeled traffic volumes, which immediately reintroduces the region specific data dependency I was trying to avoid. A trained model would be harder to explain, and explainability is the main virtue this design has to offer. And the accuracy ceiling here is set by the input data, not the model class; a gradient boosted model on top of these same eight features would produce similar rankings with none of the auditability. The assignment explicitly weights speed and simplicity over perfect accuracy, and an additive score is the simplest thing that can work.

## 5. Handling missing data

OSM tagging completeness varies enormously across the world, and any method that assumes complete tags will quietly break outside well mapped cities. My rule throughout is: impute from road class, and say so.

Lane counts and speed limits are the two commonly missing fields. When a segment is missing either one, or carries a malformed value like a lane count of zero or an unparseable speed string, the value is filled from a class based default table. A residential street defaults to one lane and 25 mph, a primary road to two lanes and 30 mph, a motorway to three lanes and 55 mph, and so on. These defaults are conservative and were chosen so that imputation never inflates a road's score above what its class alone justifies.

Critically, imputation is never silent. Every segment records whether its lanes or speed were imputed, and every road level row carries lanes_imputed_share, speed_imputed_share, and a combined data_completeness score, where 1.0 means every lane and speed value on that road was actually observed in OSM. A reader can filter or discount low completeness rows without rerunning anything.

The other missing data cases each get a specific answer. Unnamed roads are still scored and appear on the map, they are just excluded from the named road tables, since a table keyed by road name cannot represent them. If a region has no mapped transit routes or no mapped land use at all, those score terms simply contribute zero and the class based core of the score still produces a sensible ranking, which is what makes the method degrade gracefully in sparsely mapped areas rather than failing. On the network side, the Overpass fetch retries across two public mirrors and caches its response, so a mirror outage or rate limit does not kill a run. And if a bounding box genuinely contains no supported roads, the program raises a clear error instead of writing empty files.

## 6. From segments to roads

OSM stores roads as segments, and a long road like Massachusetts Avenue is dozens of them. The assignment asks for a table of roads, so segments sharing a name are aggregated, and how you aggregate matters more than it first appears.

All road level values are length weighted. A one kilometer segment counts fifty times as much as a twenty meter fragment, for the score, for every feature mean, and for every share column. Without this, a road with many tiny stub segments near intersections would have its character distorted by fragments that represent almost none of its actual length. The road score is the sum of the eight length weighted mean contributions, which preserves the property that every row is auditable by addition.

Averaging can still hide real variation, so every road row also reports the spread behind its mean: the minimum and maximum segment score, the standard deviation, the dominant category among its segments, and a consistency value giving the share of road length whose segments agree with that dominant category. Saying that Memorial Drive is High with 100 percent of its length classified High is a much stronger statement than an average alone. Each row also carries a classification margin, the distance from the road's score to the nearest category threshold, with a plain language label: under 2 points is borderline, 2 to 5 is moderate, above 5 is stable. A road scoring 38.1 sits one tenth of a point from Moderate, and presenting it with the same confidence as a road scoring 55 would be misleading, so the output does not.

The interactive map deliberately classifies per segment rather than per road, which is the more precise view for long roads that change character along their length.

## 7. Results

The primary deliverable is the Cambridge output in data/cambridge-usa/. The file results.csv contains all 667 named roads with every feature, every score contribution, and every uncertainty column. The file results.md holds the 20 road sample below, drawn from all three categories. The breakdown column lists the eight contributions in order (class + lanes + speed + one way + transit + commercial + junction + surface), and the score is exactly their sum.

### Cambridge, USA

| Road | Modal class | Mean lanes | Mean speed (mph) | Score breakdown | Score | Category | Consistency | Margin | Data completeness |
|---|---|---|---|---|---|---|---|---|---|
| Soldiers Field Frontage Road Eastbound | primary | 3.2 | 40 | 28.0 + 8.91 + 6.0 + 2.0 + 4.78 + 5.0 + 1.01 + 0.0 | 55.7 | High | 1.0 | stable | 1.0 |
| Memorial Drive | trunk | 4.0 | 36 | 34.0 + 12.0 + 4.44 + 0.0 + 0.0 + 3.66 + 1.41 + 0.0 | 55.5 | High | 1.0 | stable | 1.0 |
| Soldiers Field Road | motorway | 2.4 | 35 | 37.91 + 5.63 + 4.0 + 0.35 + 0.0 + 3.34 + 0.22 + 0.0 | 51.5 | High | 0.91 | stable | 1.0 |
| McGrath Highway | trunk | 2.2 | 38 | 32.98 + 4.7 + 5.32 + 0.34 + 1.33 + 3.94 + 0.23 + 0.0 | 48.8 | High | 0.93 | stable | 0.92 |
| Union Square | primary | 2.9 | 20 | 28.0 + 7.41 + 0.0 + 0.93 + 5.14 + 5.0 + 1.3 + 0.0 | 47.8 | High | 1.0 | stable | 1.0 |
| Eliot Bridge | motorway | 2.5 | 27 | 38.84 + 6.09 + 0.71 + 0.0 + 0.0 + 0.0 + 1.57 + 0.0 | 47.2 | High | 1.0 | stable | 1.0 |
| Prospect Street | primary | 2.3 | 26 | 28.0 + 5.4 + 0.52 + 0.0 + 6.02 + 4.64 + 1.53 + 0.0 | 46.1 | High | 1.0 | stable | 1.0 |
| Vassar Street | secondary | 2.2 | 20 | 22.0 + 4.91 + 0.0 + 0.0 + 5.0 + 5.0 + 0.68 + 0.0 | 37.6 | Moderate | 0.6 | borderline | 1.0 |
| Kirkland Street | secondary | 2.0 | 25 | 20.46 + 4.0 + 0.0 + 0.0 + 4.04 + 4.39 + 2.23 + 0.0 | 35.1 | Moderate | 0.67 | moderate | 1.0 |
| Portland Street | secondary | 2.2 | 25 | 22.0 + 5.0 + 0.0 + 0.0 + 0.0 + 5.0 + 2.81 + 0.0 | 34.8 | Moderate | 1.0 | moderate | 1.0 |
| Brattle Street | secondary | 1.8 | 22 | 23.17 + 3.23 + 0.0 + 0.98 + 1.32 + 4.52 + 1.45 + 0.0 | 34.7 | Moderate | 0.71 | moderate | 1.0 |
| North Harvard Street | secondary | 2.2 | 25 | 22.41 + 4.74 + 0.0 + 0.0 + 6.27 + 0.72 + 0.47 + 0.0 | 34.6 | Moderate | 0.55 | moderate | 1.0 |
| Medford Street | secondary | 2.0 | 28 | 22.0 + 3.9 + 1.0 + 0.44 + 2.16 + 2.42 + 2.32 + 0.0 | 34.2 | Moderate | 0.94 | moderate | 0.95 |
| Putnam Avenue | secondary | 2.0 | 20 | 22.0 + 3.86 + 0.0 + 0.07 + 0.0 + 5.0 + 3.0 + 0.0 | 33.9 | Moderate | 1.0 | moderate | 1.0 |
| Bennett Street | residential | 2.0 | 20 | 5.0 + 4.0 + 0.0 + 0.0 + 5.94 + 5.0 + 0.0 + 0.0 | 19.9 | Low | 0.5 | borderline | 1.0 |
| Walden Street | tertiary | 2.0 | 20 | 14.0 + 4.0 + 0.0 + 0.0 + 0.0 + 1.06 + 0.64 + 0.0 | 19.7 | Low | 0.6 | borderline | 1.0 |
| Oxford Street | tertiary | 1.7 | 20 | 11.51 + 2.89 + 0.0 + 0.0 + 0.0 + 4.17 + 1.16 + 0.0 | 19.7 | Low | 0.8 | borderline | 1.0 |
| Harvard Street | tertiary | 1.7 | 20 | 10.19 + 2.9 + 0.0 + 0.06 + 0.16 + 4.85 + 1.4 + 0.0 | 19.6 | Low | 0.62 | borderline | 1.0 |
| Technology Square | unclassified | 2.0 | 25 | 8.0 + 4.0 + 0.0 + 0.0 + 0.0 + 5.0 + 1.48 + 0.0 | 18.5 | Low | 1.0 | borderline | 0.5 |
| Cross Street | residential | 1.9 | 23 | 8.42 + 3.51 + 0.0 + 0.24 + 1.9 + 3.1 + 0.72 + 0.0 | 17.9 | Low | 0.75 | moderate | 1.0 |

A few rows are worth reading closely, because they show the algorithm doing something beyond echoing road class. Union Square reaches High despite a 20 mph mean speed, because heavy transit service, commercial surroundings, and multiple lanes push a primary road well past the threshold, which matches the real place. Bennett Street is a residential street that climbs to 19.9, right at the Low ceiling, on the strength of transit and commercial exposure near Harvard Square; its borderline margin label correctly flags that a small data change could flip it. Technology Square shows the completeness column earning its keep, with a 0.5 indicating half of its lane and speed input was imputed. The same seven files exist for each of the seven international cities.

## 8. Validation

Validation happened at three levels: the code, measured traffic counts, and the classifications.

**The code** is validated by an automated test suite: 15 unit tests covering the speed and lane parsers (including every OSM tagging convention the parser claims to handle), the scoring components, the category thresholds, and the geometry math. GitHub Actions runs the full suite on every push, so the repository head is always a state where the tests pass.

**Measured traffic counts.** To move past "looks plausible," the classifier is graded against real measured volumes. FHWA TMAS, the Travel Monitoring Analysis System, is the one free, nationwide, uniformly formatted count program in the United States, which makes it a good yardstick without tying the pipeline to any single state's format. It is US only, so it lives entirely in a separate validation/ module and the classifier never reads it: TMAS grades the output, it is never an input. validation/validate_tmas.py downloads the 2023 TMAS station file and all twelve monthly hourly volume files, computes AADT per continuous count station as the mean of every recorded day (requiring at least 30 days), classifies a Greater Boston window with the standard classifier, and matches each station to the nearest segment of the facility type it instruments, within 150 meters, using the station's published functional class so a freeway station cannot snap to a side street a few meters closer. Observed categories use fixed volume bins: Low under 5,000 vehicles per day, Moderate 5,000 to 20,000, High above 20,000.

Across the 11 stations with a full year of 2023 data:

| Metric | Result |
|---|---|
| Exact category agreement | 11 / 11 |
| Adjacent or better | 11 / 11 |
| Spearman rank correlation, score vs measured AADT | 0.547 |
| Measured AADT range | 41,526 to 149,226 vehicles per day |

The honest reading of that result matters as much as the number. TMAS continuous stations in this window sit almost entirely on freeways and expressways, so this confirms the High end of the scale and says nothing about the Low or Moderate boundary. And because all 11 matched roads are the same facility type, the model's class and lane features saturate, which is why the within freeway rank correlation is only moderate. What it does establish cleanly is the category claim: every road measuring above 41,000 vehicles per day is classified High. Per station detail is in validation/results_tmas.csv, with the summary in validation/summary_tmas.json.

**The classifications** were also checked qualitatively, especially in the study areas without a matching count feed. The test is whether the output matches ground truth about how roads are actually used, and in Cambridge it does: the corridors any local would name as busy (Memorial Drive, McGrath Highway, Soldiers Field Road, Massachusetts Avenue) land in High, and neighborhood side streets land in Low, with borderline cases flagged by the margin column rather than presented with false confidence. The same checks on the international cities hold without touching a single weight: Uhuru Highway and the Nairobi Expressway top the Nairobi ranking, and the Western Express Highway and the Bandra Worli Sea Link top Mumbai.

What the pipeline deliberately does not consume at runtime is region specific count data, because depending on it would break the works anywhere property. Those programs are instead the calibration path for anyone deploying this in a particular region: because the score is a linear sum of printed components, fitting proper weights against a count program is an ordinary regression, and the output tables already contain the full design matrix. The TMAS module is a working template for doing exactly that wherever the United States is in scope.

## 9. Speed and scalability

The assignment ranks speed, simplicity, and scalability above accuracy, so here is the accounting. The classifier itself runs in plain Python with only the standard library, and the only spatial operations in the entire pipeline are hash lookups, the grid cells for trip generators and the endpoint keys for junctions, so cost is linear in segment count. Cambridge's roughly 2,000 segments classify in well under a second, and the largest test city, London at 19,613 segments, takes a few seconds. Network fetch time dominates the first run of any city; every run after that is offline thanks to the cache.

The stronger scalability evidence is that the same code and the same weights ran unchanged on eight cities across five continents, from one of the best mapped places on Earth to some of the more unevenly mapped ones, and produced directionally sensible rankings in each. Because the formula is a small set of arithmetic operations, it was also ported to JavaScript inside index.html. The live search geocodes a city, retrieves its OSM data, and performs the scoring locally in the browser. Network retrieval time varies with area size and public server load, but the scoring stage stays lightweight. The observed near linear runtime suggests larger regions could be handled by tiling and parallel processing, though national or global runtime and storage were not measured.

## 10. Limitations and how I would improve it

Every design choice above trades accuracy for simplicity somewhere, and it is worth being specific about where.

The most fundamental limitation is that no measured traffic enters the pipeline, so the labels estimate likely volume rather than report counts. The TMAS validation in Section 8 confirms the High end in the US but leaves the Low and Moderate boundary unmeasured, because continuous count stations cluster on freeways. The fix is the calibration path already described: fit the component weights and category thresholds against local count data wherever a count program exists, one small regression per region, adding shorter duration counts to cover arterials and local streets.

There is no time dimension. A road is one category all day, so the model cannot distinguish rush hour from 3 a.m., or a school street at drop off from the same street at noon. Ingesting GTFS feeds where cities publish them would provide service frequencies, and headway data plus land use mix could support simple peak factors.

OSM completeness is uneven, and while imputation keeps the algorithm running in sparsely tagged areas, imputed defaults are weaker evidence than observed tags. The data_completeness column makes this visible per road. A tag free improvement would be computing betweenness centrality on the road graph, which estimates how much through traffic a road geometrically must carry using only geometry and topology; the current junction bonus is a cheap one hop stand in for exactly that.

The transit signal counts routes but ignores frequency, so a bus that comes every 10 minutes counts the same as one that comes hourly. GTFS again is the fix where available.

Name based aggregation blurs long roads, since a road that spans quiet and busy stretches averages toward the middle. The consistency, minimum, maximum, and standard deviation columns expose when this is happening, and the map sidesteps it entirely by rendering per segment, but a cleaner improvement would be splitting long roads at major junctions before aggregating.

Finally, the thresholds at 20 and 38 were tuned by inspecting Cambridge, and although they transferred to the other seven cities better than I expected, there is no guarantee they are optimal everywhere. Region specific thresholds, fit from local data or even from the local score distribution, would make the categories more comparable across cities.

## 11. Repository contents and reproduction

- classify.py holds the entire pipeline: geocoding, the single request Overpass fetch with caching and mirror fallback, feature extraction, scoring, aggregation, and all output writing. It uses only the Python standard library, and requirements.txt exists to document that there are no dependencies to install.
- tests/ contains the 15 unit tests, runnable with python -m unittest discover -s tests. GitHub Actions runs them on every push.
- validation/ contains validate_tmas.py, the FHWA TMAS grading harness from Section 8, along with the derived results_tmas.csv and summary_tmas.json. The large federal download files are cached locally and excluded from the repository.
- data/<city>/ contains, for each of the eight cities, the cached raw OSM response, results.csv with every named road and every column, results.md with the 20 road sample table, map.json with per segment scores for the map, and run_metadata.json recording the algorithm version, bounding box, thresholds, and retrieval date.
- index.html is the interactive map. It contains the same scoring formula ported to JavaScript for the live city search, which doubles as a demonstration of how small the algorithm really is.

To reproduce any city, run python3 classify.py "City, Country". One command regenerates everything, and it runs offline whenever the cache exists.

## Data attribution

Map data © OpenStreetMap contributors, available under the Open Data Commons Open Database License (ODbL). OpenStreetMap data were accessed through the Overpass API; geocoding by Nominatim; basemap tiles by CARTO. Data retrieved 2026-07-17. Validation traffic counts from the FHWA Travel Monitoring Analysis System (TMAS), 2023 station and continuous count volume files, US DOT public data.
