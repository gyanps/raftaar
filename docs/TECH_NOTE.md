# Technical note

Raftaar: Delhi bus travel times from bus GPS, and a journey planner on top. Transport Stack Hackathon 2026, Problem 1.
Numbers quoted here are produced by the pipeline; the full tables are in [VALIDATION.md](VALIDATION.md).

## 1. The starting point

The published GTFS timetable gives a route the same duration at every hour of the day: for all 1,804 routes that run in at least six different hours, the slowest and fastest scheduled hour differ by under 2%. It assumes a constant speed of roughly 18 to 20 km/h. So any planner built on the timetable alone gives the same answer at 9 am and at 11 pm. One month of bus GPS says how long the same stretches really take.

## 2. From raw rows to fixes

The archive holds 913 million rows for 1 to 31 October 2026. The feed writes a row for every bus every 10 seconds whether or not the bus has sent a new position, so a real fix is defined as one (vehicle, device timestamp); the first row seen for it is kept. Fixes outside a box around every GTFS stop are dropped (the raw feed has positions at latitude 200 and in the southern hemisphere). That leaves 561 million fixes, typically 10 seconds apart. The feed's own `speed` column disagrees with the distance between consecutive fixes and is not used; all speeds come from positions.

## 3. Linking GPS points to routes

Each fix carries a route label, but about one fix in five sits more than 60 m from that route's path (a stale label after the bus has finished, the opposite direction, a bus running empty), and almost half of the feed's trip ids match no timetable trip. So trips are rebuilt from geometry.

- **Candidates.** For each bus and day: every pattern of every route the bus was labelled with, plus the opposite direction of each.
- **Following a bus along a path.** GTFS shapes and stops are projected onto a flat metre grid centred on Connaught Place. A fix is projected onto the shape near where the bus last was: 100 m back, and ahead as far as it could have driven (25 m/s) since the previous fix. This keeps a bus on the correct pass where a route loops or returns along the same road. The direction of travel breaks the remaining ties: a fix is not assigned to a part of the shape that runs against the way the bus is moving. A fix counts as on the route within 45 m.
- **A trip** is a stretch of steady forward progress along one shape. It ends when the bus is off the shape or silent for 10 minutes, falls 200 m behind its own furthest point, jumps further than it could have driven, or stands still for 10 minutes. Leaving the published shape for a few minutes and rejoining further on is common (the shapes are not exact), so that alone does not end a trip.
- **Choosing between candidates.** Where candidates overlap in time, the longest forward run wins, with a bonus for agreeing with the feed's label. A trip needs at least 1 km, 5 stops, 70% of its fixes on the shape and under 5% backward steps. Buses that fit nothing are left out and counted, never forced onto a route.
- **Stops on shapes.** Each stop's distance along its pattern's shape is found for all stops together, so the order of stops is respected even where the shape passes the same point twice. 61 patterns with no consistent ordering are excluded.

Result: 1.75 million trips; 57 to 63% of fixes belong to a trip on any given day (the rest are mostly buses standing at depots and terminals).

## 4. What counts as travel time

For each stop on a trip, two moments are read off by interpolating between fixes: when the bus entered a zone around the stop (30 m either side, less where stops are close together) and when it left.

- **Run**: leaving one stop's zone to entering the next. Signals and congestion are inside it.
- **Dwell**: time inside a stop's zone.

They are kept separate and both are learned. A ride from stop a to stop b is the runs between them plus the dwells at the stops in between, which is what a passenger experiences. Nothing is interpolated across a gap of more than 60 seconds or 500 m: such a stretch gives no observation, but the trip total is still kept for validation. Dwell at a pattern's first and last stop is a layover, not a stop, and is never learned. A run slower than 2 km/h or faster than 70 km/h is discarded.

Runs are pooled by stop pair across every route that uses that stretch (15,188 stretches, median 600 m); 13,087 of them have GPS runs.

## 5. Travel time on each stretch

For each stretch the model holds expected seconds per metre for three kinds of day (Monday to Friday; Saturday; Sunday or public holiday) and 48 half-hours. Three things keep it sensible where data is thin:

1. **City rhythm.** All stretches share a daily rhythm. A stretch seen only at 10 am still gets a 6 pm figure: its own level times the city's 6 pm factor.
2. **Neighbouring half-hours.** A cell borrows from the half-hours either side (weights 1/4, 1/2, 1/4).
3. **Shrinkage.** A cell with n observations is pulled toward the rhythm-based figure with weight 8 / (n + 8).

Observations are first limited to between 0.4 and 3 times the stretch's own median, so one broken-down bus does not move the result. Means are stored, not medians, because means add up along a route. Dwell per stop is learned the same way. A stretch with no GPS at all takes the city's average for that time.

**A trip** is summed stretch by stretch with the clock advancing, so a long ride that starts before the evening peak and ends inside it is priced accordingly. Real trips from the training days are then replayed through the model: the ratio of actual to predicted, by trip length, day type and time band, gives a small correction and the 10th to 90th percentile range shown to the rider.

## 6. Combining live and historical data

The profile says what is usual. Live data says how today differs.

For every run seen recently, the *surprise* is log(observed / profile), minus that stretch's usual value of this log. The state of the network at a moment is the average surprise over the last 20 minutes: one number for the whole city and one for each stretch, the latter pulled toward the city's when few buses passed.

The estimate for a stretch the bus will reach h minutes from now is

    profile × exp( w_city(h) × city surprise + w_own(h) × the stretch's own surprise )

The weights are not chosen by hand. They are fitted on past days by asking how well the state at one time predicted runs that started h minutes later. Fitted on the training days of the backtest: the city-wide weight falls from 0.94 (next 10 minutes) to 0.80 (over an hour ahead); a stretch's own weight falls from 0.63 to 0.34. (The served model, fitted on all 31 days, gets 0.96 to 0.84 and 0.65 to 0.35.) In words: if the whole city is slow, it stays slow; one road's own jam is half gone in about half an hour.

**When there is little or no live data.** A stretch nobody drove in the last 20 minutes carries only the city-wide number. With no live data at all both numbers are zero and the estimate is the plain profile. Every answer therefore degrades to the historical model, never to the timetable.

**How it was checked.** The test week is replayed: each trip is predicted using only GPS that had arrived before it left. Average error falls from 3.0 to 2.7 minutes, and most on the days that were unusual (the Saturday before the festival week: 3.7 to 3.1).

**Running live.** The server polls the Transport Stack GTFS-Realtime vehicle-positions feed every 30 seconds (about 5,600 buses per poll, with the same vehicle ids, route ids and trip-id format as the October archive). Each bus keeps a rolling 90 minutes of positions, and once a minute the same matcher that built the history runs on that window to produce fresh stop-to-stop runs. A trip leaving within 10 minutes of now is answered in live mode; anything else, and everything while the feed is down or still warming up, is answered by the typical-day model, and the response says which. Two guards: live estimates are held back for the first 25 minutes after start-up, because the first buses to complete a run are the fast ones and an early average reads "faster than usual"; and a feed that fails three times in three minutes is left alone for ten.

## 7. The journey planner

A round-based search (in the style of RAPTOR) over route patterns: round k finds the earliest expected arrival at every stop using exactly k buses, up to three.

- **Waiting.** There is no trustworthy timetable to look departures up in, and the GPS shows about half the trips the timetable lists. Each pattern's buses per hour, by day type and hour, are counted from the matched trips. Headways are irregular: a rider arriving at random waits 0.88 of the average headway (0.5 would be perfectly even service). That factor is measured, not assumed.
- **Several routes on the same road.** A median pattern runs less than one bus an hour, but stretches are shared. For a boarding stop and an alighting stop, routes are pooled the way a waiting rider uses them: starting from the fastest, a route joins the set while its ride is shorter than the current expected wait plus ride.
- **Walking.** Straight-line distance × 1.3 at 4.3 km/h, up to 800 m to and from stops and 400 m between stops, with a one-minute allowance for a change.
- **Options.** One search per number of buses; an extra bus must save at least 3 minutes. A second and third search forbid the first answer's opening routes to surface a different corridor.
- **What the rider is told.** Per ride: the estimate, its range, the same ride at the quietest half-hour, and the timetable's figure.

A query typically takes about 100 ms (95% of random stop-to-stop queries under 300 ms).

## 8. Assumptions and limits

- One month of data, October 2026, with two public holidays and the run-up to Diwali; the last weekend was slower than earlier ones. Seasonal effects are not modelled.
- GTFS shapes are treated as the road. Where buses consistently take a different road, those stretches have no observations and fall back to the city average.
- It is unknown what share of the fleet reports GPS. If some buses do not, real frequencies are higher than counted and waits shorter.
- 1,272 patterns were seen fewer than twice per weekday in the GPS. They are offered at the timetable's frequency scaled by the observed ratio (0.49) and flagged in the answer.
- Walking uses straight lines, not the street network; fares, crowding and the metro are not included.
- The flat grid distorts east-west distances by up to 1% at the far northern end of the network.
- The estimate is the typical (median) trip; the slow tail is expressed in the range, not in the headline figure.
- Live sampling is one position per bus every 30 seconds (the archive has one every 10), so live stop times are a little coarser than historical ones.
- The live blend was validated by replaying October; it has not yet been scored against trips observed on the live feed itself.
- The Google Maps comparison (named pairs in VALIDATION.md) asks Google about a coming day, while the actual bus times are from October; and for some pairs Google's bus answer uses a different route between the same two stops.

## 9. Reuse

The polyline maths and the fast point-to-line index with its "which pass of the route" logic are ported from the team's own fleet-tracking code, with their tests. The idea of judging GPS fixes by device time, jitter and impossible jumps, and of detecting stops from position rather than reported speed, comes from the same place. Map geometry helpers in the browser (`web/js/MapUtils.js`) are copied from it unchanged.
