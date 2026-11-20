# Validation report

Every number below is written by `python -m raftaar.validation.report` from the files the pipeline produced (`artifacts/backtest.json`, `od_pairs.json`, `reduce_stats.json`, `match_stats.json`).

## 1. What is tested

The model is trained on 1 to 24 October 2026 and tested on 25 to 31 October, days it never saw. A test case is one rider's trip on one real bus: board at stop a, get off at stop b at least three stops later; the bus's own GPS says how long that took. The model is given only the route, the two stops and the departure time. Up to four such trips are drawn at random from every bus trip of the test week: **1,400,599 test cases** (79 dropped because the 'truth' itself was impossible, such as over 70 km/h).

Four estimates are compared:

- **Raftaar, typical day**: the travel-time profile for that kind of day and half-hour. This is what answers a question about a future time.
- **Raftaar + last 20 min of buses**: the same, adjusted by how the network was running just before the trip left, using only GPS that had already arrived by then. This is what answers 'leaving now'.
- **Same GPS, no time of day**: each stretch's all-day average. It shows how much of the gain comes from modelling the clock.
- **Published timetable**: GTFS `stop_times`, which give every route the same duration at every hour.

## 2. Headline

| Estimate | Trips | Mean trip, min | Avg error, min | Error, % of trip time | Median error, % | Bias, % | 90th pct error, min | Within 10%, % | Within 5 min, % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raftaar + last 20 min of buses | 1,400,599 | 18.77 | 2.69 | 14.4 | 10.8 | -4.3 | 6.43 | 47.0 | 85.9 |
| Raftaar, typical day | 1,400,599 | 18.77 | 3.01 | 16.0 | 11.8 | -5.7 | 7.32 | 43.6 | 83.5 |
| Same GPS, no time of day | 1,400,599 | 18.77 | 4.37 | 23.3 | 18.1 | -6.6 | 10.8 | 29.9 | 74.0 |
| Published timetable | 1,400,599 | 18.77 | 5.42 | 28.9 | 26.0 | -7.9 | 12.74 | 20.5 | 66.3 |

The typical-day model removes **44.5%** of the timetable's average error; with live data, **50.4%**. Live data removes a further 10.6% of the typical-day error.

Range shown to the rider: 77.3% of real trips fell inside the 10th to 90th percentile range (target 80%), and 46.9% inside the 25th to 75th (target 50%). The 10 to 90 range is typically 49.0% of the estimate wide.

Bias is negative because the estimate is the typical (median) trip and bus trips have a long slow tail: a few very slow trips pull the average up but not the median.

## 3. By distance, time of day and kind of day

**distance: under 3 km**

| Estimate | Trips | Mean trip, min | Avg error, min | Error, % of trip time | Median error, % | Bias, % | 90th pct error, min | Within 10%, % | Within 5 min, % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raftaar + last 20 min of buses | 537,457 | 7.21 | 1.46 | 20.3 | 13.1 | -8.7 | 3.17 | 39.9 | 94.7 |
| Raftaar, typical day | 537,457 | 7.21 | 1.61 | 22.3 | 14.1 | -10.3 | 3.6 | 37.4 | 93.6 |
| Same GPS, no time of day | 537,457 | 7.21 | 2.04 | 28.3 | 19.7 | -9.9 | 4.54 | 27.7 | 91.4 |
| Published timetable | 537,457 | 7.21 | 2.6 | 36.0 | 29.0 | -14.8 | 5.6 | 18.4 | 88.4 |

**distance: 3-8 km**

| Estimate | Trips | Mean trip, min | Avg error, min | Error, % of trip time | Median error, % | Bias, % | 90th pct error, min | Within 10%, % | Within 5 min, % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raftaar + last 20 min of buses | 568,101 | 16.9 | 2.59 | 15.3 | 10.3 | -4.8 | 5.78 | 48.7 | 87.5 |
| Raftaar, typical day | 568,101 | 16.9 | 2.88 | 17.1 | 11.3 | -6.2 | 6.59 | 45.2 | 84.9 |
| Same GPS, no time of day | 568,101 | 16.9 | 4.03 | 23.9 | 17.4 | -6.7 | 9.08 | 31.2 | 74.2 |
| Published timetable | 568,101 | 16.9 | 5.18 | 30.6 | 25.9 | -8.6 | 10.71 | 20.7 | 63.5 |

**distance: 8-15 km**

| Estimate | Trips | Mean trip, min | Avg error, min | Error, % of trip time | Median error, % | Bias, % | 90th pct error, min | Within 10%, % | Within 5 min, % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raftaar + last 20 min of buses | 217,818 | 35.87 | 4.52 | 12.6 | 8.8 | -3.3 | 9.95 | 55.2 | 71.6 |
| Raftaar, typical day | 217,818 | 35.87 | 5.09 | 14.2 | 9.9 | -4.6 | 11.47 | 50.4 | 67.2 |
| Same GPS, no time of day | 217,818 | 35.87 | 7.92 | 22.1 | 17.0 | -6.0 | 17.41 | 31.5 | 46.6 |
| Published timetable | 217,818 | 35.87 | 9.53 | 26.6 | 22.7 | -6.8 | 19.49 | 23.2 | 35.3 |

**distance: over 15 km**

| Estimate | Trips | Mean trip, min | Avg error, min | Error, % of trip time | Median error, % | Bias, % | 90th pct error, min | Within 10%, % | Within 5 min, % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raftaar + last 20 min of buses | 77,223 | 64.78 | 6.89 | 10.6 | 7.8 | -1.7 | 15.22 | 60.4 | 53.3 |
| Raftaar, typical day | 77,223 | 64.78 | 7.79 | 12.0 | 8.8 | -2.8 | 17.51 | 55.2 | 49.0 |
| Same GPS, no time of day | 77,223 | 64.78 | 13.01 | 20.1 | 16.4 | -4.5 | 28.21 | 31.9 | 28.0 |
| Published timetable | 77,223 | 64.78 | 15.2 | 23.5 | 20.1 | -3.2 | 31.84 | 25.9 | 21.8 |

**time: night 00-06**

| Estimate | Trips | Mean trip, min | Avg error, min | Error, % of trip time | Median error, % | Bias, % | 90th pct error, min | Within 10%, % | Within 5 min, % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raftaar + last 20 min of buses | 49,719 | 11.87 | 1.44 | 12.1 | 9.0 | -3.0 | 3.33 | 54.1 | 94.9 |
| Raftaar, typical day | 49,719 | 11.87 | 1.46 | 12.3 | 9.2 | -2.0 | 3.35 | 53.3 | 94.8 |
| Same GPS, no time of day | 49,719 | 11.87 | 5.97 | 50.3 | 50.2 | 47.8 | 13.95 | 6.7 | 60.1 |
| Published timetable | 49,719 | 11.87 | 5.96 | 50.2 | 54.7 | 47.2 | 13.38 | 5.5 | 58.5 |

**time: early 06-08**

| Estimate | Trips | Mean trip, min | Avg error, min | Error, % of trip time | Median error, % | Bias, % | 90th pct error, min | Within 10%, % | Within 5 min, % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raftaar + last 20 min of buses | 157,354 | 15.09 | 1.6 | 10.6 | 8.6 | -0.7 | 3.69 | 56.2 | 94.2 |
| Raftaar, typical day | 157,354 | 15.09 | 1.71 | 11.3 | 9.3 | -0.2 | 4.01 | 53.2 | 93.3 |
| Same GPS, no time of day | 157,354 | 15.09 | 4.78 | 31.7 | 31.5 | 28.7 | 11.2 | 14.5 | 67.0 |
| Published timetable | 157,354 | 15.09 | 4.69 | 31.1 | 32.4 | 25.2 | 10.62 | 15.4 | 67.8 |

**time: morning 08-11**

| Estimate | Trips | Mean trip, min | Avg error, min | Error, % of trip time | Median error, % | Bias, % | 90th pct error, min | Within 10%, % | Within 5 min, % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raftaar + last 20 min of buses | 291,602 | 19.7 | 2.3 | 11.7 | 9.2 | -2.3 | 5.41 | 53.5 | 88.7 |
| Raftaar, typical day | 291,602 | 19.7 | 2.52 | 12.8 | 10.0 | -2.5 | 6.04 | 50.0 | 86.9 |
| Same GPS, no time of day | 291,602 | 19.7 | 3.32 | 16.9 | 14.2 | 1.1 | 7.95 | 37.1 | 80.1 |
| Published timetable | 291,602 | 19.7 | 4.33 | 22.0 | 19.7 | -2.1 | 10.17 | 27.2 | 72.4 |

**time: midday 11-16**

| Estimate | Trips | Mean trip, min | Avg error, min | Error, % of trip time | Median error, % | Bias, % | 90th pct error, min | Within 10%, % | Within 5 min, % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raftaar + last 20 min of buses | 391,095 | 18.75 | 2.91 | 15.5 | 11.7 | -5.5 | 6.91 | 44.0 | 84.5 |
| Raftaar, typical day | 391,095 | 18.75 | 3.26 | 17.4 | 13.0 | -6.9 | 7.88 | 40.2 | 81.6 |
| Same GPS, no time of day | 391,095 | 18.75 | 3.75 | 20.0 | 14.2 | -13.4 | 9.38 | 37.2 | 78.7 |
| Published timetable | 391,095 | 18.75 | 5.18 | 27.6 | 24.1 | -13.9 | 12.22 | 22.1 | 67.7 |

**time: evening 16-21**

| Estimate | Trips | Mean trip, min | Avg error, min | Error, % of trip time | Median error, % | Bias, % | 90th pct error, min | Within 10%, % | Within 5 min, % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raftaar + last 20 min of buses | 369,707 | 22.82 | 3.73 | 16.3 | 12.8 | -5.8 | 9.07 | 40.6 | 78.3 |
| Raftaar, typical day | 369,707 | 22.82 | 4.26 | 18.7 | 14.4 | -8.6 | 10.6 | 36.6 | 74.5 |
| Same GPS, no time of day | 369,707 | 22.82 | 6.01 | 26.3 | 19.3 | -23.0 | 15.81 | 27.6 | 65.4 |
| Published timetable | 369,707 | 22.82 | 7.39 | 32.4 | 28.2 | -23.8 | 18.35 | 17.8 | 56.9 |

**time: late 21-24**

| Estimate | Trips | Mean trip, min | Avg error, min | Error, % of trip time | Median error, % | Bias, % | 90th pct error, min | Within 10%, % | Within 5 min, % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raftaar + last 20 min of buses | 141,122 | 12.82 | 1.89 | 14.7 | 11.2 | -4.7 | 4.45 | 45.6 | 91.6 |
| Raftaar, typical day | 141,122 | 12.82 | 2.03 | 15.8 | 11.8 | -5.5 | 4.87 | 43.6 | 90.4 |
| Same GPS, no time of day | 141,122 | 12.82 | 2.9 | 22.7 | 19.9 | 9.4 | 6.79 | 26.8 | 83.4 |
| Published timetable | 141,122 | 12.82 | 3.75 | 29.2 | 27.4 | 10.1 | 8.65 | 20.1 | 76.1 |

**day: weekday**

| Estimate | Trips | Mean trip, min | Avg error, min | Error, % of trip time | Median error, % | Bias, % | 90th pct error, min | Within 10%, % | Within 5 min, % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raftaar + last 20 min of buses | 991,827 | 18.79 | 2.65 | 14.1 | 10.8 | -3.3 | 6.31 | 47.0 | 86.1 |
| Raftaar, typical day | 991,827 | 18.79 | 2.9 | 15.5 | 11.8 | -3.1 | 7.01 | 43.6 | 84.1 |
| Same GPS, no time of day | 991,827 | 18.79 | 4.25 | 22.6 | 17.5 | -6.9 | 10.5 | 30.9 | 74.7 |
| Published timetable | 991,827 | 18.79 | 5.32 | 28.3 | 25.5 | -8.2 | 12.52 | 20.8 | 66.7 |

**day: saturday**

| Estimate | Trips | Mean trip, min | Avg error, min | Error, % of trip time | Median error, % | Bias, % | 90th pct error, min | Within 10%, % | Within 5 min, % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raftaar + last 20 min of buses | 200,732 | 19.84 | 3.13 | 15.8 | 11.7 | -7.6 | 7.6 | 44.1 | 83.0 |
| Raftaar, typical day | 200,732 | 19.84 | 3.65 | 18.4 | 12.8 | -12.6 | 9.25 | 40.7 | 79.4 |
| Same GPS, no time of day | 200,732 | 19.84 | 5.29 | 26.7 | 20.4 | -12.5 | 13.24 | 26.6 | 69.0 |
| Published timetable | 200,732 | 19.84 | 6.31 | 31.8 | 28.1 | -13.6 | 15.0 | 18.8 | 62.7 |

**day: sunday_holiday**

| Estimate | Trips | Mean trip, min | Avg error, min | Error, % of trip time | Median error, % | Bias, % | 90th pct error, min | Within 10%, % | Within 5 min, % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Raftaar + last 20 min of buses | 208,040 | 17.63 | 2.5 | 14.2 | 10.2 | -6.3 | 5.9 | 49.3 | 87.6 |
| Raftaar, typical day | 208,040 | 17.63 | 2.88 | 16.3 | 11.0 | -11.1 | 7.12 | 46.2 | 84.7 |
| Same GPS, no time of day | 208,040 | 17.63 | 4.06 | 23.0 | 19.0 | 1.6 | 9.98 | 28.4 | 75.1 |
| Published timetable | 208,040 | 17.63 | 5.0 | 28.4 | 26.3 | -0.4 | 11.76 | 20.5 | 68.2 |

## 4. How long a live reading stays useful

Fitted on the last ten training days: how much of a surprise seen now still holds for a stretch the bus reaches later.

| Minutes ahead | 0-10 | 10-20 | 20-40 | 40-60 | 60+ |
|---|---:|---:|---:|---:|---:|
| City-wide surprise | 0.943 | 0.917 | 0.874 | 0.837 | 0.805 |
| A stretch's own surprise | 0.633 | 0.537 | 0.456 | 0.397 | 0.338 |

If the whole city is slow now it will mostly still be slow in an hour; one road's own jam is half gone in about half an hour.

## 5. One level down: single stop-to-stop runs

On 400,000 single runs from the test days (mean 1.65 min): average error 0.46 min with the profile, 0.54 min without time of day. Single runs are noisy (one red light is a large share of a 90-second run); errors partly cancel over a trip, which is why trips are the unit that matters.

## 6. What the data covered

| | |
|---|---:|
| Days | 31 |
| Raw rows read | 912,877,061 |
| Real GPS fixes after removing re-logged rows and positions outside Delhi | 560,802,819 |
| Fixes that belong to a rebuilt trip | 343,097,170 (61%) |
| Bus trips rebuilt | 1,746,662 |
| Stop arrivals and departures read off | 37,445,634 |
| Stop-to-stop stretches with GPS runs in training | 13,024 of 15,188 |
| Test runs on stretches never seen in training | 0.0% |
| Route patterns whose frequency comes from GPS | 2,165 of 3,437 |
| Bus trips seen in GPS per timetable trip (on patterns seen) | 0.489 |

The fixes outside trips are mostly buses standing at depots and terminals, buses running empty, and buses whose route label does not match where they are. They are left out rather than forced onto a route.

## 7. Named pairs: actual bus times, Raftaar, the timetable, and Google Maps

Pairs are chosen by rule (the busiest route patterns, spread across the city, plus the brief's own example). 'Actual' is the median of real buses that left the first stop within 30 minutes of the stated time on the test days (25 to 31 October), with the 10th to 90th percentile in brackets. Raftaar's figure comes from the model trained on 1 to 24 October.

| Route | From → To | km | When | Buses | Actual, min | Raftaar, min | Timetable, min | Google, on the bus | Google, by car |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| 425UP | Red Fort → Nehru Place Terminal | 14.5 | weekday 08:30 | 20 | 44.6 (40.3–50.1) | 45.5 | 46.2 | 49.2 | 31.7 |
| 425UP | Red Fort → Nehru Place Terminal | 14.5 | weekday 22:00 | 4 | 38.1 | 38.6 | 46.2 | 49.2 | 31.9 |
| 78DOWN | Shadipur Depot → Subash Place Depot | 8.9 | weekday 08:30 | 32 | 22.9 (20.8–26.8) | 23.5 | 27.3 | 22.0 | 10.7 |
| 78DOWN | Shadipur Depot → Subash Place Depot | 8.9 | weekday 13:00 | 21 | 27.9 (22.8–31.7) | 30.0 | 27.3 | 22.0 | 13.8 |
| 78DOWN | Shadipur Depot → Subash Place Depot | 8.9 | weekday 18:30 | 36 | 39.9 (32.0–49.6) | 40.5 | 27.3 | 22.0 | 17.0 |
| 78DOWN | Shadipur Depot → Subash Place Depot | 8.9 | weekday 22:00 | 18 | 26.6 (20.9–30.7) | 25.1 | 27.3 | 22.0 | 12.7 |
| 78DOWN | Shadipur Depot → Subash Place Depot | 8.9 | sunday 13:00 | 3 | 37.2 | 25.3 | 27.3 | 22.0 | 12.5 |
| MC715DOWN | Bhalaswa Crossing / Bhalaswa Golf Course → Kali Mata Mandir | 7.3 | weekday 08:30 | 26 | 16.9 (15.5–18.5) | 18.1 | 24.0 | 25.2 | 11.1 |
| MC715DOWN | Bhalaswa Crossing / Bhalaswa Golf Course → Kali Mata Mandir | 7.3 | weekday 13:00 | 10 | 28.7 (22.0–36.4) | 25.3 | 24.0 | 25.1 | 19.9 |
| MC715DOWN | Bhalaswa Crossing / Bhalaswa Golf Course → Kali Mata Mandir | 7.3 | weekday 18:30 | 17 | 27.9 (24.8–33.3) | 28.9 | 24.0 | 25.1 | 23.7 |
| 525STLDOWN | Maidan Garhi Crossing (SDM Court) → Surajkund Crossing | 8.5 | weekday 08:30 | 31 | 27.9 (22.9–32.2) | 27.2 | 27.9 | 29.9 | 24.1 |
| 525STLDOWN | Maidan Garhi Crossing (SDM Court) → Surajkund Crossing | 8.5 | weekday 13:00 | 13 | 28.9 (24.0–32.7) | 29.9 | 27.9 | 29.9 | 27.4 |
| 525STLDOWN | Maidan Garhi Crossing (SDM Court) → Surajkund Crossing | 8.5 | weekday 18:30 | 19 | 47.9 (44.1–52.4) | 39.6 | 27.9 | 29.9 | 31.9 |
| 525STLDOWN | Maidan Garhi Crossing (SDM Court) → Surajkund Crossing | 8.5 | weekday 22:00 | 9 | 29.8 (25.9–33.5) | 24.9 | 27.9 | 29.9 | 22.9 |
| 525STLDOWN | Maidan Garhi Crossing (SDM Court) → Surajkund Crossing | 8.5 | sunday 13:00 | 6 | 33.7 (30.6–37.4) | 25.5 | 27.9 | 29.9 | 24.5 |
| 546STL-GHD-IIDOWN | Pandwala Crossing → Sewak Park Dwarka More Metro Station | 11.0 | weekday 08:30 | 34 | 37.3 (31.2–45.6) | 36.2 | 35.8 | 38.0 | 30.6 |
| 546STL-GHD-IIDOWN | Pandwala Crossing → Sewak Park Dwarka More Metro Station | 11.0 | weekday 13:00 | 13 | 39.3 (33.7–40.7) | 40.5 | 35.8 | 38.0 | 30.8 |
| 546STL-GHD-IIDOWN | Pandwala Crossing → Sewak Park Dwarka More Metro Station | 11.0 | weekday 18:30 | 7 | 67.9 (55.8–75.3) | 52.5 | 35.8 | 38.0 | 36.5 |
| GL-23DOWN | Hasanpur Depot → Shastri Park GT Road | 7.9 | weekday 08:30 | 30 | 22.8 (21.0–26.1) | 22.6 | 25.5 | 26.9 | 15.0 |
| GL-23DOWN | Hasanpur Depot → Shastri Park GT Road | 7.9 | weekday 13:00 | 16 | 24.9 (23.5–27.9) | 26.6 | 25.5 | 27.1 | 19.5 |
| GL-23DOWN | Hasanpur Depot → Shastri Park GT Road | 7.9 | weekday 18:30 | 24 | 31.2 (27.7–35.5) | 32.4 | 25.5 | 27.1 | 21.4 |
| GL-23DOWN | Hasanpur Depot → Shastri Park GT Road | 7.9 | weekday 22:00 | 12 | 22.8 (20.8–27.5) | 21.9 | 25.5 | 27.1 | 16.1 |
| GL-23DOWN | Hasanpur Depot → Shastri Park GT Road | 7.9 | sunday 13:00 | 3 | 27.6 | 23.1 | 25.5 | 27.1 | 18.0 |
| 861AUP | Tilak Nagar Crossing → Sarai Peepal Thala | 14.1 | weekday 08:30 | 30 | 42.6 (37.5–49.5) | 43.8 | 44.2 | 47.0 | 27.1 |
| 861AUP | Tilak Nagar Crossing → Sarai Peepal Thala | 14.1 | weekday 13:00 | 17 | 59.6 (49.3–75.8) | 55.6 | 44.2 | 47.0 | 34.5 |
| 861AUP | Tilak Nagar Crossing → Sarai Peepal Thala | 14.1 | weekday 18:30 | 20 | 73.4 (52.1–81.5) | 67.6 | 44.2 | 47.0 | 37.1 |
| 861AUP | Tilak Nagar Crossing → Sarai Peepal Thala | 14.1 | weekday 22:00 | 5 | 45.7 (39.7–53.0) | 41.0 | 44.2 | 47.0 | 28.4 |
| 861AUP | Tilak Nagar Crossing → Sarai Peepal Thala | 14.1 | sunday 13:00 | 3 | 48.4 | 44.4 | 44.2 | 47.0 | 29.4 |
| 8UP | Madanpur Khadar Crossing → SALARPUR VILLAGE | 12.9 | weekday 08:30 | 4 | 35.3 | 28.9 | 40.2 | 66.7 | 29.0 |
| 8UP | Madanpur Khadar Crossing → SALARPUR VILLAGE | 12.9 | weekday 18:30 | 18 | 44.8 (36.7–61.7) | 40.5 | 40.2 | 66.7 | 40.7 |
| D-5403DOWN | Mayur vihar Phase 3 Crossing → Lalita Park | 10.1 | weekday 08:30 | 17 | 31.9 (27.3–34.9) | 33.4 | 27.2 | 28.7 | 18.6 |
| D-5403DOWN | Mayur vihar Phase 3 Crossing → Lalita Park | 10.1 | weekday 13:00 | 21 | 32.8 (29.4–38.1) | 35.4 | 27.2 | 28.7 | 20.2 |
| D-5403DOWN | Mayur vihar Phase 3 Crossing → Lalita Park | 10.1 | weekday 18:30 | 8 | 37.2 (33.5–40.6) | 44.0 | 27.2 | 28.7 | 26.9 |
| D-5403DOWN | Mayur vihar Phase 3 Crossing → Lalita Park | 10.1 | weekday 22:00 | 25 | 27.9 (25.9–31.4) | 28.5 | 27.2 | 28.7 | 18.6 |
| D-5403DOWN | Mayur vihar Phase 3 Crossing → Lalita Park | 10.1 | sunday 13:00 | 7 | 32.2 (30.9–35.5) | 31.9 | 27.2 | 28.7 | 19.1 |
| 433UP | Krishan Menon Marg → Okhla Phase 1 | 15.1 | weekday 08:30 | 19 | 50.6 (45.9–55.6) | 48.8 | 46.7 | 47.8 | 36.1 |
| 433UP | Krishan Menon Marg → Okhla Phase 1 | 15.1 | weekday 13:00 | 5 | 49.9 (44.0–57.0) | 54.4 | 46.7 | 47.8 | 39.8 |
| 433UP | Krishan Menon Marg → Okhla Phase 1 | 15.1 | weekday 18:30 | 10 | 88.9 (64.6–109.2) | 76.8 | 46.7 | 47.8 | 60.8 |
| 433UP | Krishan Menon Marg → Okhla Phase 1 | 15.1 | weekday 22:00 | 5 | 42.6 (36.8–52.6) | 39.9 | 46.7 | 55.8 | 31.8 |
| ML-96DOWN | DSIDC Flats → Jahangirpuri Metro Station GT Road | 14.7 | weekday 08:30 | 14 | 33.1 (29.6–46.6) | 31.4 | 48.9 | 51.4 | 19.5 |
| ML-96DOWN | DSIDC Flats → Jahangirpuri Metro Station GT Road | 14.7 | weekday 13:00 | 5 | 36.3 (33.6–46.4) | 36.2 | 48.9 | 51.4 | 26.4 |
| ML-96DOWN | DSIDC Flats → Jahangirpuri Metro Station GT Road | 14.7 | weekday 18:30 | 14 | 55.1 (46.9–63.0) | 40.9 | 48.9 | 51.4 | 30.3 |
| ML-96DOWN | DSIDC Flats → Jahangirpuri Metro Station GT Road | 14.7 | weekday 22:00 | 12 | 29.6 (27.3–35.7) | 30.4 | 48.9 | 50.1 | 21.3 |
| 33UP | Jyoti Nagar Enclave / DURGA PURI CHOWK → Telephone Exch Noida Sector-19 | 20.9 | weekday 08:30 | 10 | 79.2 (70.9–97.2) | 72.3 | 66.2 | 71.5 | 47.7 |
| 33UP | Jyoti Nagar Enclave / DURGA PURI CHOWK → Telephone Exch Noida Sector-19 | 20.9 | weekday 13:00 | 4 | 74.2 | 68.6 | 66.2 | 74.7 | 47.1 |
| 33UP | Jyoti Nagar Enclave / DURGA PURI CHOWK → Telephone Exch Noida Sector-19 | 20.9 | weekday 18:30 | 3 | 78.8 | 80.7 | 66.2 | 57.5 | 55.7 |
| 33UP | Jyoti Nagar Enclave / DURGA PURI CHOWK → Telephone Exch Noida Sector-19 | 20.9 | weekday 22:00 | 7 | 54.7 (50.6–58.9) | 51.2 | 66.2 | 63.1 | 37.5 |
| 918DOWN | Peera Garhi Chowk → Kamala Market /  Ajmeri Gate | 14.0 | weekday 08:30 | 27 | 53.8 (42.5–78.5) | 49.9 | 45.6 | 48.6 | 34.2 |
| 918DOWN | Peera Garhi Chowk → Kamala Market /  Ajmeri Gate | 14.0 | weekday 18:30 | 17 | 90.9 (77.6–104.8) | 72.7 | 45.6 | 48.1 | 43.7 |
| 918DOWN | Peera Garhi Chowk → Kamala Market /  Ajmeri Gate | 14.0 | weekday 22:00 | 3 | 37.8 | 46.4 | 45.6 | 48.0 | 34.3 |
| 717ADOWN | Kathuria Public School/Vasant Kunj D7 → Tughlaqabad Village | 14.0 | weekday 08:30 | 15 | 50.8 (43.0–60.5) | 48.9 | 45.6 | 48.5 | 41.6 |
| 717ADOWN | Kathuria Public School/Vasant Kunj D7 → Tughlaqabad Village | 14.0 | weekday 13:00 | 8 | 55.8 (49.6–60.4) | 54.8 | 45.6 | 48.5 | 42.4 |
| 717ADOWN | Kathuria Public School/Vasant Kunj D7 → Tughlaqabad Village | 14.0 | weekday 18:30 | 7 | 75.8 (58.7–86.6) | 71.4 | 45.6 | 48.5 | 52.3 |
| 717ADOWN | Kathuria Public School/Vasant Kunj D7 → Tughlaqabad Village | 14.0 | weekday 22:00 | 7 | 41.3 (38.9–50.3) | 41.8 | 45.6 | 48.5 | 35.4 |

Over these 54 rows, average distance from the actual median: Raftaar 3.9 min, timetable 9.0 min, Google's bus answer 9.7 min, Google by car 14.3 min.

Between the quietest and the busiest weekday time, the same pair's bus ride changed by a median of **25.0 min** on real buses, **16.6 min** in Raftaar's estimates and **0.0 min** in Google's bus answers (12 pairs). Google's bus times barely move with the clock, as the timetable's do not.

'Google, on the bus' is the time on the bus in Google's bus-only answer for the same two stops, leaving at that time on the next day of that kind; 'Google, by car' is its driving time with its traffic forecast, a view of the road itself. Google is asked about a coming day, the actual figures are from October.
