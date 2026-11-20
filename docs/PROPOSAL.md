# Proposal

**Team:** Gyan Pratap Singh, Khalid Khan
**Problem chosen:** Problem 1, real-time bus traffic model and journey planner.

## The gap

Delhi's published bus timetable gives each route one duration for the whole day, at a constant speed of roughly 18 to 20 km/h. A commuter planning with it gets the same answer at 9 am and 11 pm. On route 425 from Kashmere Gate to Nehru Place the timetable says 54 minutes; the model, built from what buses did on each stretch, says about 39 minutes at 3 am and 90 at 6 pm.

## What we built

1. **A travel-time model** learned from one month of bus GPS: for each of 15,188 stop-to-stop stretches, the expected time by half-hour and by kind of day, with time at stops learned separately.
2. **A live adjustment**, fed by the real-time vehicle-positions feed, that shifts the estimate by how the network ran in the last 20 minutes, with weights learned from history for how fast such information goes stale.
3. **A journey planner and API** that returns walk, board, ride, change and walk for up to three options, with a time range, the same trip at the quietest hour, and the timetable's figure.
4. **A map screen**: plan a trip; see bus speed on every stretch of the network for any half-hour; watch the live buses; see the accuracy tables.

## Approach in one paragraph

Buses are followed along their route shapes by distance, so each bus yields the time it reached and left every stop. Because the feed's route labels and trip ids are unreliable, trips are rebuilt from geometry. Stretches are pooled across routes that share them. Profiles borrow strength from the city's daily rhythm where a stretch has little data. Trips are sums of stretches with the clock advancing. The planner takes waiting times from how often buses were really seen, and pools routes that share a road because a rider takes the first useful bus.

## Data used

- DTC bus GPS, 1 to 31 October 2026 (913 million rows; 561 million real fixes; 1.75 million trips rebuilt).
- Static Delhi bus GTFS (stops, routes, shapes, stop times).
- The Delhi buses GTFS-Realtime vehicle-positions feed, for live mode.
- OpenStreetMap, for the base map only.

## Results so far

On 1.4 million real bus trips from days the model never saw: average error 3.0 minutes for a typical-day estimate and 2.7 minutes with the last 20 minutes of GPS, against 5.4 minutes for the timetable. Details in [VALIDATION.md](VALIDATION.md); method in [TECH_NOTE.md](TECH_NOTE.md).

## What comes next

Score the live adjustment against trips observed on the live feed itself; add the Google Maps comparison for the named pairs; show next-bus times at a stop from the buses approaching it; replace straight-line walking with the street network; add metro legs.
