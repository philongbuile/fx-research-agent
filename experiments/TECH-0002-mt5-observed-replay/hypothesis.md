# TECH-0002: bounded MT5 data replay

Technical question: can a checksum-bound, live-observed MT5 minute-bar window be
normalized, stored immutably, exported and delivered by the pinned LEAN engine
without changing bid OHLC or bar timing?

This is not a trading hypothesis. There are no signals, positions, simulated or
broker orders, tuned parameters, OOS evaluation, promotion or edge claim.

Freeze before the first run: use the two captured observation anchors selected
for this technical check; admit only full contiguous minutes strictly between
their UTC observations. Require a fresh, advancing quote clock with an unchanged
integral-minute offset, same weekday/date and a short window. Preserve the raw
captures and both checksums. Reject missing, duplicate, unordered or invalid
bars; never fill. No current-offset inference is permitted outside this window,
and historical DST remains unverified.

Existing D-17 EURUSD M1 observations are the transport input. D-17's future
strategy hypothesis on aggregated hour bars is separate. The explicit pip
metadata convention is not used for sizing: this algorithm submits no orders.

Success requires valid raw-store checksums, technical-only export provenance,
read-only data/algorithm mounts and network-disabled LEAN, plus exact expected
bid OHLC/open-time/end-time delivery and the replay completion marker. Failure
of any check invalidates this technical result. Every engine invocation is a
recorded trial, including failures, and none may be deleted.

Costs: reconstructed ask OHLC uses the MT5 minute minimum spread; intrabar spread
is unknown. Commission, slippage and historical swap are not evaluated because
there are no orders/positions. OANDA calendars/properties are export plumbing,
not a representation of the demo source's execution conditions. The chosen
observed intraday weekday window avoids any session boundary; the full-day
quality report must show that most of the day is outside the observation.

Frozen inputs for the first trial:

- First anchor capture: `data/mt5-quarantine/20261028T103404Z-eb4eba49/capture.json`,
  SHA-256 `fd77d89c291fe5743eadb08d81478aa187b022a8ed42a9f2f415971c5d3237f1`.
- Last anchor/data capture: `data/mt5-quarantine/20261028T104013Z-ff549131/capture.json`,
  SHA-256 `b335451e8bc259de0df1cb926cedb9c8627748414ae108aa647d445132681b6e`.
- Independent UTC clock tool observations agreed with the capture host at both
  ends. This supports only the contemporaneous interval, not historical DST.
- Research config: `lean/MT5DataReplay/research.toml`; no variant selection or OOS.

Outcome and exact run ID will be recorded after the frozen implementation runs.

## Initialization failure and next frozen transport trial

`20261028T104052Z-e392aa1e` failed during initialization: the pinned engine clamps
end dates to yesterday in the algorithm calendar. Same-day UTC observations
therefore cannot initialize under a UTC algorithm calendar. No market evaluation
or OOS result was seen, and the failed trial is retained.

The next transport trial uses NodaTime's fixed-offset `UTC+13:15` algorithm
calendar, whose already-closed local day contains the original observed UTC window.
Source timestamps, bar-open/end-time checks, requested data dates and stored
provenance remain UTC; neither the engine nor the host clock is changed. Reject
an observation outside that closed local day. This is calendar configuration for
a technical replay, not a new strategy or evidence of edge.

The original capture refs and exported observations above remain frozen. This
calendar was chosen only to put the technical test's day boundary after those
closed UTC bars. It is not a geographic/source timezone, a DST model, or a
strategy setting; do not reuse it as a broker calendar. The data subscription
retains its UTC data/exchange time and replay checks use `utc_time`. The engine's
normal end-date clamp remains active and a too-recent window is rejected.

`20261028T104814Z-09503c1c` reached data delivery but failed the first timestamp
comparison. Inspection of the pinned image's market-hours database confirmed
`dataTimeZone=UTC` and `exchangeTimeZone=America/New_York` for OANDA forex.
QuoteBar labels must be converted from the exchange zone back to UTC before
comparison; this does not change source/export timestamps. Retain the failed
trial. The next trial corrects this harness comparison with unchanged input,
calendar, costs and no orders; no strategy or OOS result was evaluated.

`20261028T104951Z-cdb61afe` then failed on Python's aware/naive comparison with
LEAN's UTC callback clock. Normalize both comparison operands consistently;
regression tests cover aware and naive UTC callbacks with exchange-local bars.
The same inputs remain frozen. This failed technical trial is retained as well.
