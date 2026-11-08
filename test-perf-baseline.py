"""L6 — performance & scale acceptance layer (Phase 52).

Why this layer exists
---------------------
L1..L5 all ask "is the behaviour true?". None asks "does the service stay usable
while it works, and does it degrade gracefully as the data root grows?". A system
can pass five layers while one 120 MB snapshot freezes every other console tab, and
while importing the 175th asset costs 17x more than the 25th. Phase 52 measured both
on this codebase before fixing anything: the RED baseline run (p52_l6_run1.out) held a
canary /health poll 1375.4 ms against a 32.8 ms same-run idle p95 (41.9x) while a
snapshot of 120 MB ran, and the pre-Phase-52 spike had measured 22.8 ms -> 395 ms of
cost per imported asset as the corpus grew.

Every criterion here is a RATIO, never an absolute millisecond figure
--------------------------------------------------------------------
Milliseconds move with the machine, the disk and the antivirus, so no check asserts
one. Each check compares two quantities measured in the SAME run on the SAME box:
  * starvation legs (three, all same-run, none needing an idle CLIENT baseline) -- for
                     an operation that is expected to sit entirely OFF the loop (snapshot
                     copy, delivery ZIP, image import, search burst): the tail p95/p50 of
                     the canary polls taken while it runs, the wall fraction
                     p95_during/wall-of-the-operation, and the loop-lag excess the SERVICE
                     recorded for its own event loop inside the window -- each wake's
                     lateness LESS this run's quiet-loop floor, averaged (and, once the
                     window holds MIN_SAMPLES wakes, also at its p95) and divided by that
                     window's unit of work. A single long block shows up in the tail, the
                     wall fraction and the loop-lag max; many medium blocks show up in the
                     loop-lag mean. Neither shape dominates the other -- stake m1 (one
                     120 MB copy back on the loop) reads wall fraction 0.99 and a loop-lag
                     mean 0.88x of the snapshot's own 254 ms wall, while its tail stays at
                     1.0x because a 0.25 s window lands one canary poll (the tail leg is
                     skipped below MIN_SAMPLES for exactly that reason); stake m4 (the whole
                     embeddings DB rewritten by every one of 20 imports) reads tail 4.3x
                     with a per-add cost scaling ratio of 2.95x, i.e. the second defect is
                     all but invisible to the legs that catch the first.
                     The leg that used to be here third -- sustain, p50_during/idle_p50 --
                     was retired by measurement, see _starve().
                     Why the first edition's single "canary / idle p95" ratio is gone:
                     that denominator ranges 4.9-21.8 ms between healthy runs of the same
                     code, so the same product scored 5.6x, 4.7x, 3.3x and 8.1x against
                     itself and the checks went red as collateral damage.
  * unit-of-work ratio -- the canary during an operation, divided by the SERVICE'S OWN
                     measured cost for one unit of that same operation (one cosine
                     scoring pass, one file of the batch). Used for the two GIL-bound
                     CPU kernels, because an idle-ratio there is not just fragile, it
                     is self-contradicting: after Phase 52 offloaded the blocking work,
                     the idle p95 collapsed from 32.8 ms (run1) to 6.5 ms (run2) while
                     the search burst's own canary p95 improved 219.9 ms -> 22.0 ms,
                     and the *ratio* still went from 6.7x to 3.4x -- i.e. the check got
                     redder as the product got better. The denominator IS the defect
                     being fixed, so it cannot be the yardstick. A unit-of-work ceiling
                     scales with the machine the way the kernel does.
  * scaling ratio -- per-unit cost at a grown corpus / per-unit cost at a small one,
                     both taken as the MEDIAN of the per-operation samples. "O(N^2)" is
                     machine-independent; "395 ms" is not.
  * counters, not clocks -- where the product exposes a measured counter (peak
                     concurrency, in-flight, workers applied, route hits), the check
                     asserts the counter. It is the only criterion family here that no
                     busy machine can move, and it is what L6-C3a/C4 became after two
                     false reds on a wall-clock ratio whose three legs execute
                     identically by design. The loop-lag watchdog the service now runs on
                     its own event loop belongs to this family too, which is the reason it
                     replaced L6-B4's client-side statistic: with 12 request threads in
                     flight, what the client measures is its own GIL (clean 175.7 ms vs the
                     loop-blocking defect's 175.1 ms -- the same number on product and
                     defect), while a task sleeping on the loop being measured is reached by
                     no client traffic at all.
One convention applies to the noisiest leg (L6-B4, whose numerator is a window the
service reports and whose denominator is a query time the server reports): the leg is run
REPEATS times and the least-contaminated sample is judged. Interference only ever ADDS
latency, so the best of N identical legs is the honestest estimate of the product -- and
the aggregation only works on a quantity a defect inflates in EVERY leg. That is what
stake m3 measured: with the MEDIAN-as-numerator edition, m3 (the cosine loop put back on
the event loop) read 0.34 / 0.52 / 1.07 and passed a 0.70 ceiling on its best leg, i.e.
min-of-N discarded the signal. The statistic was changed for that reason, not because a
clean run went red.
Both loop-lag ceilings are now PINNED against that convention rather than set beside it, on
the burst leg -- the only window where this statistic has enough wakes to be a distribution.
Five sustained runs whose SEARCH path is the correct product (clean run15, clean run16, and
stakes m0/m1/m2, whose mutations sit in the snapshot and in the batch grant respectively)
put the JUDGED mean -- the least-contaminated of three legs, which is what the check
asserts -- at 0.33 / 0.36 / 0.25 / 0.34 / 0.29, worst 0.36. Stake m3, the defect this
ceiling exists for, judged 0.63 on one run and 0.58 on another, with every leg above the pin
in both. The geometric midpoint of those two measured populations is 0.457, so the mean
ceiling is 0.45: 25% clear of the worst healthy judged leg and 29% clear of the defect's
best, and it catches m3 on EVERY leg, leaving min-of-N nothing to rescue. Unjudged healthy
legs reach further (0.49 on run16, 0.42 on m1) and that is printed in every detail line
rather than hidden -- it is the cost of the same convention, not a second verdict. The p95
of the same legs does NOT separate the populations (judged healthy 0.46-1.00 against defect
1.18 and 1.37, an 18% gap, with unjudged healthy legs up to 1.66), so it is pinned at 1.5
as a gross-tail guard and no verdict depends on it; a p95 ceiling at 1.0 would have sat
inside the correct product's own spread, which is the run9 mistake in a new costume.
Seven further judged readings were then taken after L6-C1's ingest window was widened (the
burst leg now runs over a 250-asset corpus instead of 175): clean run22 and run23 judged
0.34 and 0.33, and the five re-run stakes judged 0.36 / 0.31 / 0.28 / 0.31 / 0.32, so the
healthy worst is still 0.36, while stake m3 judged 0.68 with all three of its legs (0.85 /
0.68 / 0.76) above the pin. Neither population moved when the corpus grew, which is why this
pin did not have to be re-derived for the widened suite.
The same discipline retired two wall-clock ratios: L6-C3a's wall(4)/wall(1) and L6-C3c's
canary(8)/canary(1) each compared legs the product executes identically by design, so
their spread was always the machine's; both are reported, neither is asserted.
L6-C1, the ingest scaling leg, was re-derived the same way and for the same reason: its
first edition divided the median per-add cost at a grown corpus by the median per-add cost
at 25 assets and pinned the ceiling at 1.75 over a 25 -> 175 span. The cost-curve spike
(outputs/p52_c1_spike.out) then measured that the CORRECT product is itself O(N) per add --
it rewrites the assets snapshot every add -- so over a short span a growth ratio compares
two linear curves while the fixed per-add cost dominates and cannot see the difference
between their slopes: five clean runs read 1.09 / 1.20 / 1.21 / 1.25 / 1.46 while stake m4
(the whole-embeddings-DB rewrite put back on the hot path) read 1.66, i.e. a ceiling 5%
above the defect and a correct product within 17% of it. Widening the span to 25 -> 250
widens exactly the quantity the check is about: the spike reads clean 2.05 against defect
6.48 there, and in-suite clean 1.15 / 1.31 / 1.34 / 0.89 / 1.32 / 1.36 / 1.43 (seven runs)
against m4 3.57 / 3.58 / 3.68, so
the ceiling is pinned at 2.7 -- 24% clear of the highest clean reading in any condition
measured and 32% clear of the defect's lowest. Note the 0.89: this ratio is diluted by any
fixed per-add cost the machine is paying at the time (both windows share it), so it can go
under one on a correct product; the physical quantity underneath, the marginal cost of one
extra asset in the corpus, is 0.02-0.06 ms clean against 0.42-0.50 on the defect, and it is
printed unasserted because an absolute ms/asset is machine-speed talk.
Where a limit is physics rather than a defect it is labelled as such: a pure-Python
flood fill holds the GIL, so no threading scheme can make /health unpaged while it
runs. The check that goes red is the one the product CAN fix (an unbounded or
cross-request-leaking worker grant), and the absolute GIL cost is reported, not
asserted.

The GIL floor was measured, not assumed (outputs/p52_gil_spike.out, 2 reps per variant,
same workload: 6 x 900x700 remove_background at workers=1):
      sys.setswitchinterval   canary p50   canary p95   per-file kernel cost
      5.0 ms (CPython default)   97 ms        195 ms          1807 ms
      0.2 ms                     12 ms         45 ms          2411 ms  (+33%)
      0.1 ms                     12 ms         41 ms          2542 ms  (+41%)
      0.05 ms                    11 ms         41 ms          2324 ms  (+29%)
So the floor has a price-tagged lever: pre-empting the GIL 25x more often cuts the
console's wait ~4x and costs 30-41% on every image operation. V1.0 does NOT take that
trade (the batch endpoint is serialised anyway, so the wait is a background poll, while
the throughput cost is on the product's primary workflow); it is recorded here so the
next phase can decide with numbers instead of opinion.

The static auditor carries its own calibration pair (a violation that MUST be
reported and the identical call behind to_thread that MUST NOT be), because a bare
count of 0 is indistinguishable from a broken instrument.

Coverage
--------
  A  static loop-blocking audit over agent_service.py + all *_engine.py, with an
     exactly-matched exemption list (a stale exemption is itself a failure)
  B  canary ratios: 120 MB snapshot / delivery package / large-image import burst /
     search burst / batch at 1 and 8 workers, + "returns to idle baseline" after
  C  scaling and honesty of the knobs: ingest growth over a 25 -> 250 corpus, retrieval
     at-or-below
     linear, the GIL-bound batch serialised across the SERVICE and measured by a
     counter (never by the wall clock it cannot move), the batch self-reports measured
     parallel efficiency + the worker budget actually applied, and one service-wide
     budget bounds concurrent batches
  D  budgets loose enough to travel: source cold start, console-poll repeat cost,
     RSS growth over the whole load set. A missing instrument or artifact is reported
     as SKIP with the reason -- never as PASS.

Runs against the source service, serially, on an avoided port, in an isolated
ZAOJING_DATA_DIR. Exit code 1 on any FAIL.
"""
import ast
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(errors="backslashreplace")
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
SERVICE = OUT / "agent_service.py"

# ---- the criterion families, each pinned by a measured clean-vs-defect distribution --
# RATIO-TO-IDLE IS NOT ONE OF THEM. The first edition of this layer gated B1/B2/B3/B6 on
# "canary p95 <= 3x this run's idle p95", and the mutation stakes proved that yardstick is
# machine-lottery: the idle p95 of a correctly-offloaded service ranged 4.9 ms (stakes m1,
# m3) to 21.8 ms (m0) from run to run, so the SAME product scored 5.6x, 4.7x, 3.3x and 8.1x
# against itself and the ratio checks went red as collateral damage on runs where nothing
# was starved. Denominator collapse: fixing loop starvation collapses the denominator.
# What survived re-derivation are three signals that are all measured INSIDE one run and
# need no idle baseline at all:
TAIL_MAX = 12.0     # p95_during / p50_during -- a single long block blows the tail up while
                    # leaving the median alone (defects 43.9x / 64.3x / 117.6x / 129.6x;
                    # clean max 6.1x on m2's snapshot run). Only judged on a sample of at
                    # least MIN_SAMPLES polls -- see _starve().
SUSTAIN_MAX = 3.0   # RETIRED AS AN ASSERT (kept only as the printed reference). This was
                    # p50_during / idle_p50, the designed catcher for "many medium blocks".
                    # Nine clean readings of the SAME correct snapshot window put that
                    # numerator at 4.1-15.4 ms (0.9 / 1.0 / 1.2 / 1.9 / 3.5x of idle) while
                    # the defect it exists for (stake m4) reads 4.2x -- a ceiling one poll
                    # above normal machine variation, and run9 went RED at 3.5x on code with
                    # nothing starved. See _starve() for the full argument and the numbers.

WALL_FRAC = 0.5     # p95_during / wall -- one block may not be most of the request (defect
                    # run1 0.98-1.00, m1 0.92; clean <= 0.12)
TAIL_MATERIAL_WALL_FRAC = 0.25
                    # Phase 58 re-derivation of the tail leg's RED condition: a tail above
                    # TAIL_MAX is only charged when the same window's worst poll also exceeds
                    # this share of the operation's own wall clock. The number is not new --
                    # 0.25 is the ceiling _starve() already tightens the wall leg to when the
                    # window is short on samples ("a poll longer than a quarter of the whole
                    # operation's wall clock is a stall no poll count can excuse"), and the wall
                    # leg below now reads THIS constant so the shared pin is one line of code,
                    # not one line of prose.
                    # Measured reason, enumerated from disk by p58_b2_population.py (receipt
                    # p58_b2_population.json: it globs every *L6*/*.l6* file and reads each L6-B2
                    # verdict line's own detail line -- no hand-picked file list -- and it SPLITS
                    # the readings by whether that run reached an `L6 TOTAL` line, listing the runs
                    # that never printed one separately instead of counting them as samples.
                    # Snapshot at the time of writing (re-read the receipt; this is a point-in-time
                    # count, not a constant -- p58_b2_citation_check.py re-derives every number in
                    # this comment from that receipt and reads the suite RED where they disagree):
                    # TWENTY adjudicating readings, split by provenance into NINE clean readings
                    # (no stake driver touched that run and its own TOTAL line printed no FAILs),
                    # FOUR red-leg readings and SEVEN stake readings.
                    # What the red-leg bucket is, and why it is not evidence for a ceiling: those
                    # are runs whose own TOTAL line printed at least one FAIL, so the leg that went
                    # red sat elsewhere in their load set -- the P55 first pass, the P52 run11
                    # repeat, the P58 six-layer first pass, and the stake driver's unpatched
                    # control. Keeping them out of the pinned population is not a hedge: a run that
                    # reported a failure anywhere is a run whose machine state this suite cannot
                    # vouch for, and folding them into the ceiling sample would let a red leg of a
                    # broken run define what a normal tail looks like.
                    # The only reading above TAIL_MAX in the adjudicating set is 20.1x, and the
                    # receipt files it as a declared stake run rather than as a slice of machine
                    # luck: canary p50 6.6 ms, p95 132.1 ms, n=30 -- past MIN_SAMPLES, so the
                    # small-sample gate neither saved it nor tried to -- wallfrac 0.11, under this
                    # constant, so the joint gate exempts the leg and the check prints the
                    # exemption instead of the failure. That run's driver logged the same mutation
                    # four minutes earlier at 1.4x and, later, at 3.1x (p58_d4v2_4b_red_L6.log and
                    # p58_d4v2_4b_red2_L6.log), so the swing lives INSIDE one mutation's own legs:
                    # a fact about the machine, the same off-CPU family that made D4 v1 unreadable,
                    # recorded here under the run that measured it -- because a number filed under
                    # the wrong run cannot be re-checked against anything, which is exactly how the
                    # first edition of this comment came to describe a stake leg as healthy code
                    # catching a bad hour, sitting between two runs of byte-identical product code
                    # reading 1.4x and 2.6x. The gap this rule has to bridge is therefore 2.9x ->
                    # 20.1x in tail with 0.02 -> 0.11 in wall fraction, and 0.25 sits above both.
                    # What the joint rule gives up, stated plainly: a block that occupies LESS
                    # than a quarter of its own window is no longer charged by the tail leg. It
                    # is still charged here by the loop-lag leg, which is asserted independently
                    # and exists exactly for partial blocks (its mean leg asserts at any sample
                    # count) -- that is why widening the tail leg is not lowering the bar.
                    # EVIDENCE LIMIT, honestly recorded: the two defect-side numbers quoted in
                    # this suite's criterion text (the P52 RED baseline 129.6x at wallfrac 1.00
                    # and stake m1's 43.9x at 0.99) have NO measured detail line left on disk.
                    # Scope of that claim, re-measured rather than recalled: a recursive grep of
                    # this workspace for "p95/p50=" over every .log/.out/.json finds 129.6 and 43.9
                    # ONLY inside the criterion text that each run prints, never inside a
                    # "wall=… canary p50=…" detail line -- so 129.6/43.9 survive only as the text
                    # pinned into the assertion when those runs were current. The highest MEASURED
                    # B2 detail line still on disk is the 20.1x stake leg named above, then 4.3x
                    # and 3.4x from the stake driver's own run-record files; detail lines of OTHER
                    # legs reach 85.0x (a B1 leg the sample gate skipped for n<25) and 10.5x, so
                    # "the ceiling looks low elsewhere" is not a B2 reading and is not counted
                    # here. Their staying charged is therefore proven by the
                    # counterfactual arms A3/A4 in p58_b2_joint_eval.py (which eval the criterion
                    # lines read off this file, and report flipped_by_new_term) and by the live
                    # stake m1 re-run, NOT by a surviving log.
                    # Falsification of this constant itself: p58_b2_joint_eval.py arms A6a/A6b
                    # put wallfrac exactly 0.25 (exempt) against 0.2501 (charged), so the strict
                    # ">" is measured, and A2/A6a are the only arms whose verdict differs from the
                    # pre-Phase-58 criterion -- the term is load-bearing, not decoration.
MIN_SAMPLES = 25    # tail leg is only judged on at least this many canary polls (see
                    # _starve(); the prober lands ~1 poll per unit of time, so this is
                    # roughly "the operation took at least a second")
P50_FLOOR = 5.0     # ms -- the suite's own observed round-trip floor for /health on this
                    # service (idle medians measure 3.7-7.7 ms). A canary median at or below
                    # it means nothing is queueing, so tail-vs-median is undefined there and
                    # a p95 is a single event, not a distribution
POLL_SLEEP = 0.02   # canary prober sleep (unchanged from the first edition of this layer)
POLL_CYCLE = 0.025  # nominal prober cycle for the CLIENT-SIDE cadence-excess reading, which this
                    # layer now PRINTS and no longer asserts (see LOOP_LAG_MEAN_CEIL for what replaced
                    # it and the measured reason): POLL_SLEEP plus the round-trip floor this suite
                    # observes on an idle service (3.7-7.7 ms). The excess subtracts it from the gap
                    # between successive poll STARTS, so it reports time spent QUEUED in front of a
                    # loop rather than the time a handler legitimately takes.
LOOP_LAG_MEAN_CEIL = 0.45  # PINNED from four sustained-window readings on disk (clean run15, clean
                          # run16, stake m1, stake m3), never by lowering a ceiling to fit a
                          # failing clean run. What
                          # is asserted on L6-B1/B2/B3/B4 is the MEAN and the p95 of the SERVICE'S
                          # OWN event-loop-lag samples inside the measured window, each LESS this
                          # run's quiet-loop floor, divided by that window's unit of work. The floor
                          # is subtracted per sample, not divided, and it is the same instrument:
                          # over one second of total silence the watchdog still wakes 10.9-14.8 ms
                          # late on this box against a 50 ms heartbeat, which is Windows' 15.6 ms
                          # clock tick, not the product holding the loop. Asserting the raw lateness
                          # would measure the operating system's scheduler. Why the service rather
                          # than the client measures it: the fourth edition judged a cadence excess
                          # timed in THIS process, and stake m3 showed the numerator had no defect
                          # content in it at all -- clean run11 read lag p95=175.7 ms and the
                          # loop-blocking defect read p95=175.1 ms over the same 12-client burst,
                          # because with 12 request threads in flight what gets late is the SUITE's
                          # own GIL. Separation came only from the denominator (75.2 ms vs 46.1 ms
                          # per query), i.e. from the machine, not the statistic. A watchdog that
                          # sleeps on the service's loop and reports how late it woke can be reached
                          # by no client-side traffic: it is a counter the product keeps about the
                          # product, the same family as peak_concurrency.
                          # THE PIN, on the burst leg where the statistic has real content. FIVE
                          # sustained runs whose SEARCH path is the correct product (clean run15,
                          # clean run16, stake m0's unmutated control, stake m1 -- whose mutation
                          # sits in the snapshot, and stake m2 -- whose sits in the batch grant)
                          # put the JUDGED mean (the least-contaminated of three legs, which is
                          # what this check asserts -- interference only ever ADDS latency, see the
                          # header) at 0.33 / 0.36 / 0.25 / 0.34 / 0.29, worst 0.36. Stake m3 -- the
                          # cosine kernel back on the event loop, the defect this ceiling exists
                          # for -- reads 0.63 judged on one run and 0.58 on another, with every one
                          # of its legs above the pin in both. The geometric midpoint of the two
                          # measured populations (0.36 .. 0.58) is 0.457, so the pin is 0.45: 25%
                          # above the worst healthy judged leg and 29% below the defect's best.
                          # Recording the thin side honestly: an UNjudged healthy leg reached 0.49
                          # (run16) and 0.42 (m1), which no verdict uses, and the defect's margin
                          # would fall to 13% if its mean drifted up to the earlier pin at 0.50 --
                          # which is exactly what the second m3 run showed (0.58 against 0.63), so
                          # the pin was moved toward the middle of the populations, not toward a
                          # failing clean run. The short windows are far from the pin on both
                          # sides: B1/B2/B3 read 0.00-0.01x on the clean product (per-sample floor
                          # subtraction), and stake m1's blocked snapshot window reads 0.88x.
LOOP_LAG_P95_CEIL = 1.5   # PINNED as a gross-tail guard, NOT as the discriminating statistic, and
                          # the measurements are why: on the same ~30-wake windows the p95 does not
                          # separate the two populations. Judged-leg p95 read 0.68 / 1.00 / 0.46 /
                          # 0.75 / 0.69 across the five healthy runs (worst 1.00) against 1.18 and
                          # 1.37 for stake m3 -- an 18% gap, far too thin to put a verdict on; and
                          # the UNjudged legs of healthy runs wander above the pin (1.66 on stake
                          # m1, 1.29 on the same run, 1.04 on m2), which the least-contaminated-leg
                          # rule absorbs. A ceiling at 1.0 would sit INSIDE the correct product's
                          # own spread (the run9 lesson wearing a new costume), so the mean carries
                          # the verdict and this fires only on a tail worse than one and a half
                          # scoring passes; asserted only once the window holds MIN_SAMPLES wakes.
B4_CLIENTS = 12     # concurrent searches in the L6-B4 burst (dynamic-range argument in the
                    # comment above the burst helper)
B4_MIN_WINDOW_S = 2.5   # L6-B4 keeps firing 12-client waves until the window is at least this
                        # long, for a reason measured on run13 rather than assumed: one wave closed
                        # in 0.33 s on this box, which let the service's 50 ms watchdog land only
                        # n=5 samples inside the window -- and at n=5 a p95 IS the maximum, the
                        # same small-sample trap this layer gates with MIN_SAMPLES on the canary
                        # tail leg (see B1's text). Judged on a single wake, the clean product read
                        # 0.46x (max 90.6 ms) and stake m3 read 1.21x (max 162.0 ms): the ordering
                        # was right but the quantity was one unlucky wake. The burst shape (always
                        # B4_CLIENTS in flight) is unchanged; only its duration is floored, so the
                        # asserted p95 is a quantile over >= 25 wakes.
B4_MAX_WAVES = 12       # safety cap on the sustained burst: never fire more waves than this even
                        # if the window floor has not been reached. The leg's own wake count is
                        # printed, so a machine that closes 12 waves inside 2.5 s (and therefore
                        # lands short of MIN_SAMPLES wakes) would show it rather than hide it.

GIL_UNIT_CEIL = 1.0     # CPU-kernel checks: canary p95 must stay under one unit of the
IDLE_SAMPLES = 60   # both baselines are sampled this often (see B6's note)
C1_SMALL_N = 25       # L6-C1 small window: 25 adds onto an empty corpus
C1_BIG_N = 225        # L6-C1 big window: 225 more adds, so the grown corpus is 250. SECOND
                      # EDITION of this check. The first edition used 150 (corpus 175) and the
                      # spike p52_c1_spike.out measured why that is too short a span to
                      # discriminate: the CLEAN product is itself O(N) per add (it rewrites the
                      # assets snapshot every add), so a growth ratio over 25 -> 175 compares two
                      # linear curves inside a span where the fixed per-add cost still dominates,
                      # and read 1.09 / 1.20 / 1.21 / 1.25 / 1.46 clean against 1.66 on the
                      # stake-m4 defect -- a ceiling ON the defect, i.e. the L6-B4 first-edition
                      # failure mode. Over 25 -> 250 the same spike reads 2.05 clean against 6.48
                      # on the defect, a 3.2x separation bought with ~6 s of clean runtime.
C1_MEDIAN_CEIL = 2.7   # PINNED from the two distributions this statistic has, over the widened
                       # 25 -> 250 window, and never by lowering a ceiling to fit a failing clean
                       # run. Correct product, seven in-suite runs (run18-run24): 1.15 / 1.31 /
                       # 1.34 / 0.89 / 1.32 / 1.36 / 1.43 -- that 0.89 shows the dilution law
                       # directly: a transient made the 25-asset window cost MORE than the
                       # 250-asset one, so the ratio can go UNDER one; the cost-curve spike
                       # p52_c1_spike.out, which runs the same
                       # ingest path with no prior legs on the service and therefore a much
                       # smaller fixed per-add cost, reads 2.05 for the same 25 -> 250 span. That
                       # 2.05 is the number the pin has to clear, because a lighter machine is a
                       # real condition, not a hypothetical. Stake m4 (the whole-DB rewrite put
                       # back on the hot path) reads 3.57 / 3.58 / 3.68 in-suite and 6.48 in the
                       # spike: its floor is 3.57. Geometric midpoint of the binding extremes
                       # (2.05 clean ceiling-of-reach, 3.57 defect floor) is 2.71, so the pin sits
                       # 24% above the highest clean reading in ANY condition measured and 32%
                       # below the lowest defect reading. The first edition's 1.75 over 25 -> 175
                       # had 5% margin on the defect and -20% on the clean spread.
REPEATS = 3         # L6-B4 runs its leg this many times per session and keeps the least
                    # contaminated sample (see the note above the B4 check)

# ---- Phase 58 D4 v2 constants (see the check text for the measurements behind each) ----
D4_SAMPLES = 24     # per endpoint, round-robin inside one window (same-round comparison)
D4_TAIL_MS = 300.0  # the only absolute-millisecond number kept from D4 v1, and demoted:
                    # it no longer adjudicates a median, it counts tail EVENTS
D4_TAIL_MAX = 1     # of D4_SAMPLES, how many may land above D4_TAIL_MS
D4_RATIO = 3.0      # p50(server time) of a polled endpoint / p50(server time) of /health
D4_ABS_MS = 5.0     # floor for that bound, for the same reason B6 exempts a median that sits
                    # at the suite's own observation floor (see the B6 note)
D4_GROSS_MS = 300.0  # D4 v1's openapi bound, carried over unchanged as the warm-up guard
D4_WAIT_S = 5.0      # >= 2 x the product's snapshot interval, so "strictly increasing" is a
                     # statement about a background worker, not about a lucky ordering


def a(s):
    return str(s).encode("ascii", "replace").decode("ascii")


def _answered(p):
    try:
        r = requests.get("http://127.0.0.1:%d/api/v1/health" % p, timeout=2)
        j = r.json()
    except Exception:
        return False
    return r.status_code == 200 and isinstance(j, dict) and isinstance(j.get("engines"), dict)


def _pick_port(candidates=(8881, 8883, 8885, 8887, 8889, 8891)):
    for p in candidates:
        if not _answered(p):
            return p
    raise RuntimeError("no silent L6 port among %s" % (candidates,))


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    return xs[min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))]


def _fnum(v):
    """Format a loop-lag ratio that may legitimately be ABSENT (no counter, no unit of work).

    Printed as "absent" rather than 0.00 on purpose: a statistic this layer divides by must never
    be able to look like a perfect score because the instrument was missing (the L6-B0 reason)."""
    return "absent" if v is None else "%.2f" % v


PORT = _pick_port()
BASE = "http://127.0.0.1:%d" % PORT
tmp = Path(tempfile.mkdtemp(prefix="zjl6_"))
DATA = tmp / "data"
FIX = tmp / "fix"
FIX.mkdir(parents=True)

results, skipped = [], []


def check(name, cond, detail=""):
    tag = "[PASS]" if cond else "[FAIL]"
    results.append((tag, name, detail))
    print("%s %s\n      %s" % (tag, a(name), a(detail)))


def skip(name, reason):
    skipped.append((name, reason))
    print("[SKIP] %s -- %s" % (a(name), a(reason)))


# ==========================================================================
# A. static audit
# ==========================================================================
OFFLOAD_FUNCS = {"to_thread", "run_in_executor", "to_interpreter_thread"}
MODULE_ATTRS = {
    "shutil": {"copy", "copy2", "copyfile", "move", "rmtree", "make_archive", "disk_usage"},
    "zipfile": {"ZipFile"},
    "subprocess": {"run", "check_output", "call", "Popen", "check_call"},
    "requests": {"get", "post", "put", "delete", "patch", "head"},
    "time": {"sleep"},
    "os": {"system", "remove", "rename", "replace", "makedirs", "startfile", "chmod"},
    "hashlib": {"sha256", "md5", "sha1", "sha512"},
    "socket": {"create_connection"},
    "fontTools.ttLib": {"TTFont"},
    # Phase 58: the two names this layer's own instrumentation measured, after they had been
    # invisible to the table for six phases. p58_l6d4_parts.json put 11.2-14.5 ms on a quiet
    # psutil.Process.num_threads() and 27.2-144.3 ms once the machine's total thread count grew
    # (its handler_body_total tracked it sample for sample), and p58_l6d4_report.json put
    # shutil.disk_usage at 2.0x the /metrics median and 4.7x its max while a sustained 4 MB/fsync
    # write ran on the data volume -- with /health, /capabilities and /openapi.json standing
    # still at 1.2-1.3 / 3.8-3.9 / 2.3-2.5 ms in the same window. Neither name was in the table,
    # so neither could produce an offender while the calls sat on the loop.
    "psutil": {"Process", "virtual_memory", "cpu_percent", "disk_usage", "net_if_addrs",
               "pids_iter", "process_iter"},
}
# every word below appeared in the P52 measurement set -- PIL pixel work, file
# reads/writes, engine persistence. `convert` is listed because PIL images use it.
OBJ_ATTRS = {"open", "read_bytes", "read_text", "write_bytes", "write_text", "save",
             "thumbnail", "resize", "crop", "convert", "load", "getdata", "putdata",
             "save_databases", "dump"}
NON_IO_NAMES = {"dumps", "loads"}          # json.dumps into a string is not I/O
# (file, async def, attribute) triples that the auditor sees but that are pure
# calculation. The list must match reality exactly: an exemption that no longer
# has a matching hit is a stale exemption and fails the check.
EXEMPT = {("agent_service.py", "color_convert", "convert")}

AUDIT_FILES = [SERVICE] + sorted(OUT.glob("*_engine.py"))


def _literal_blocking_calls(node):
    """Blocking calls written directly in a function body (no offload awareness)."""
    found = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call) or not isinstance(sub.func, ast.Attribute):
            continue
        recv = f.value.id if isinstance((f := sub.func).value, ast.Name) else None
        if f.attr in NON_IO_NAMES:
            continue
        if recv in MODULE_ATTRS and f.attr in MODULE_ATTRS[recv]:
            found.append((recv + "." if recv else "") + f.attr)
        elif f.attr in OBJ_ATTRS:
            found.append(f.attr)
    return found


def _sync_defs_by_name(tree):
    """{name: [FunctionDef, ...]} for every SYNC def in the file (module level and
    methods). These are the bodies a direct call from an `async def` executes on the
    event loop -- the one-hop shape the literal-only audit could not see (L6-A0's
    third calibration pair, pinned by the P52 mutation stake m1/m3: both mutations
    moved work back onto the loop by calling a sync helper directly, and a helper
    call is not a literal blocking call)."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            out.setdefault(node.name, []).append(node)
    return out


def _callee_is_blocking(name, sync_defs, depth, seen):
    """True when calling the sync def `name` from the loop performs blocking work,
    looking `depth` hops through same-file sync callees."""
    if name in seen or depth <= 0:
        return False
    seen = seen | {name}
    for fn in sync_defs.get(name, ()):
        if _literal_blocking_calls(fn):
            return True
        for sub in ast.walk(fn):
            if not isinstance(sub, ast.Call):
                continue
            f = sub.func
            callee = None
            if isinstance(f, ast.Name):
                callee = f.id
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                    and f.value.id == "self":
                callee = f.attr
            if callee and _callee_is_blocking(callee, sync_defs, depth - 1, seen):
                return True
    return False


def _blocking_calls_in_async_defs(source, fname):
    """[(async_func, line, call_name)] for blocking calls sitting directly on the
    event loop inside an `async def` -- that is, neither inside an offload call nor
    inside a nested sync def that is only ever *handed to* an offload call (that body
    is worker code running on another thread; charging it to the loop would be a false
    positive and would push the product toward pointless edits).

    Two shapes are charged: a literal blocking call (shutil.copyfile, im.save, ...)
    and a direct call to a same-file SYNC helper that performs one (transitively, up
    to 3 hops). The second shape is what the Phase 52 mutation stakes showed the first
    edition was blind to: `await asyncio.to_thread(self._stage_backup, ...)` ->
    `self._stage_backup(...)` puts a 120 MB copy back on the loop and a literal-only
    auditor reports nothing."""
    hits = []
    tree = ast.parse(source)
    module_sync_defs = _sync_defs_by_name(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        offloaded = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                    and sub.func.attr in OFFLOAD_FUNCS:
                offloaded.update(id(x) for x in ast.walk(sub))
        worker_body = set()
        for nd in (n for n in ast.walk(node)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not node):
            nd_nodes = {id(x) for x in ast.walk(nd)}
            refs = [r for r in ast.walk(node) if isinstance(r, ast.Name)
                    and r.id == nd.name and id(r) not in nd_nodes]
            if refs and all(id(r) in offloaded for r in refs):
                worker_body |= nd_nodes
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call) or id(sub) in offloaded or id(sub) in worker_body:
                continue
            f = sub.func
            if not isinstance(f, ast.Attribute):
                if isinstance(f, ast.Name) and f.id in module_sync_defs \
                        and _callee_is_blocking(f.id, module_sync_defs, 3, frozenset()):
                    hits.append((node.name, sub.lineno, f.id))
                continue
            recv = f.value.id if isinstance(f.value, ast.Name) else None
            if f.attr in NON_IO_NAMES:
                continue
            keyed = (recv + "." if recv else "") + f.attr
            if recv in MODULE_ATTRS and f.attr in MODULE_ATTRS[recv]:
                hits.append((node.name, sub.lineno, keyed))
            elif f.attr in OBJ_ATTRS:
                hits.append((node.name, sub.lineno, f.attr))
            elif recv == "self" and f.attr in module_sync_defs \
                    and _callee_is_blocking(f.attr, module_sync_defs, 3, frozenset()):
                hits.append((node.name, sub.lineno, "self." + f.attr))
    return hits


def _audit(paths):
    out, parse_err = [], []
    for p in paths:
        try:
            out += [(p.name, fn, ln, key) for fn, ln, key
                    in _blocking_calls_in_async_defs(p.read_text(encoding="utf-8"), p.name)]
        except SyntaxError as e:
            parse_err.append((p.name, str(e)))
    return out, parse_err


_BAD = ("import shutil\nasync def handler():\n    shutil.copyfile('a', 'b')\n    return 1\n")
_GOOD = ("import asyncio, shutil\nasync def handler():\n"
         "    await asyncio.to_thread(shutil.copyfile, 'a', 'b')\n    return 1\n")
# the same call inside a nested worker def, once handed to an executor (not loop code)
# and once called directly (loop code) -- the ruler must tell these apart, because the
# executor-passed-closure shape is legitimate here (AssetEngine._generate_thumbnail
# shipped a `_make` closure handed to run_in_executor until Phase 52 moved the whole
# method off the loop), and charging such a body to the loop would punish correct code
_NESTED_OFF = ("import asyncio\nasync def handler():\n"
               "    def _make():\n        import shutil; shutil.copyfile('a', 'b'); return 1\n"
               "    return await asyncio.get_event_loop().run_in_executor(None, _make)\n")
_NESTED_ON = ("async def handler():\n"
              "    def _make():\n        import shutil; shutil.copyfile('a', 'b'); return 1\n"
              "    return _make()\n")
# the one-hop shape: a SYNC method of the same file that performs the blocking call,
# reached directly from the loop vs handed to to_thread. This pair exists because the
# Phase 52 mutation stakes proved it: stake m1 replaced
# `await asyncio.to_thread(self._stage_backup, ...)` with `self._stage_backup(...)` --
# a 120 MB copy back on the event loop -- and a literal-only auditor said nothing,
# because the blocking call lives in the callee's body, not at the call site.
_HELPER_SRC = ("import shutil\n"
               "class C:\n"
               "    def _stage(self):\n"
               "        shutil.copyfile('a', 'b')\n"
               "        return 1\n")
_HELPER_ON = _HELPER_SRC + "    async def h(self):\n        return self._stage()\n"
_HELPER_OFF = (_HELPER_SRC + "    async def h(self):\n        import asyncio\n"
               "        return await asyncio.to_thread(self._stage)\n")
_bad_hits = _blocking_calls_in_async_defs(_BAD, "calib_bad.py")
_good_hits = _blocking_calls_in_async_defs(_GOOD, "calib_good.py")
_noff = _blocking_calls_in_async_defs(_NESTED_OFF, "calib_nested_off.py")
_non = _blocking_calls_in_async_defs(_NESTED_ON, "calib_nested_on.py")
_hon = _blocking_calls_in_async_defs(_HELPER_ON, "calib_helper_on.py")
_hoff = _blocking_calls_in_async_defs(_HELPER_OFF, "calib_helper_off.py")
check("L6-A0 auditor self-calibration (three non-vacuity pairs, six verdicts): a bare "
      "shutil.copyfile inside an async def MUST be reported and the identical call behind "
      "await asyncio.to_thread MUST NOT; a nested worker def MUST be charged to the loop "
      "when it is called directly but NOT when it is only handed to run_in_executor; and a "
      "direct call to a same-file sync helper MUST be charged while that same helper behind "
      "to_thread MUST NOT -- without these a count of 0 would be indistinguishable from a "
      "broken instrument, and a count of everything would punish code that is already correct",
      len(_bad_hits) == 1 and _bad_hits[0][0] == "handler"
      and _bad_hits[0][2] == "shutil.copyfile" and _good_hits == []
      and _noff == [] and len(_non) == 2 and {h[2] for h in _non} == {"_make", "shutil.copyfile"}
      and len(_hon) == 1 and _hon[0][2] == "self._stage" and _hoff == [],
      "bad=%s good=%s nested_off=%s nested_on=%s helper_on=%s helper_off=%s"
      % (a(_bad_hits), a(_good_hits), a(_noff), a(_non), a(_hon), a(_hoff)))

_hits, _perr = _audit(AUDIT_FILES)
_seen = {(f, fn, key) for f, fn, _ln, key in _hits}
_offenders = sorted(_seen - EXEMPT)
_stale = sorted(EXEMPT - _seen)
_by_file = {}
for _f, _fn, _ln, _k in _hits:
    _by_file.setdefault(_f, []).append("%s@%s %s" % (_fn, _ln, _k))
check("L6-A1 no un-offloaded blocking call sits on the event loop anywhere in the hot path "
      "(every async def in agent_service.py + all %d *_engine.py files). Exemptions are "
      "explicit, and a stale exemption fails too. What violating this costs was measured, "
      "not asserted: in the RED baseline run (p52_l6_run1.out) 23 such calls sat on the loop "
      "and a 120 MB snapshot held a /health poll to 1375.4 ms against that run's 32.8 ms idle "
      "p95 (41.9x)"
      % (len(AUDIT_FILES) - 1),
      _perr == [] and _offenders == [] and _stale == [],
      "parse_err=%s files=%d raw_hits=%d offenders=%s stale_exemptions=%s %s"
      % (a(_perr), len(AUDIT_FILES), len(_hits), a(_offenders), a(_stale),
         a(json.dumps(_by_file, ensure_ascii=False))[:520]))

# ==========================================================================
# harness
# ==========================================================================
env = dict(os.environ, ZAOJING_PORT=str(PORT), ZAOJING_DATA_DIR=str(DATA))
srv_log = open(OUT / "p52_l6_service.log", "w", encoding="utf-8", errors="replace")
_t = time.perf_counter()
proc = subprocess.Popen([sys.executable, str(SERVICE)], env=env, cwd=str(ROOT),
                        stdout=srv_log, stderr=subprocess.STDOUT)
READY = None
while time.perf_counter() - _t < 90:
    if _answered(PORT):
        READY = time.perf_counter() - _t
        break
    time.sleep(0.2)

LAT, LAG, GO = [], [], {"v": False}


def _prober():
    # Two readings per poll, deliberately different in kind:
    #   LAT -- how long one /health call took (a DURATION the console user feels)
    #   LAG -- how far the poll START drifted past the nominal cadence (the excess a blocked
    #          server loop pushes onto the next tick). Kept as a PRINTED reference only: stake
    #          m3 measured both quantities' ceiling -- the client-side excess saturates at this
    #          process's own thread pool during a concurrent burst (175.7 ms clean vs 175.1 ms
    #          on the defect), and the round-trip median sits on the transport floor. The
    #          service's own watchdog span is what _starve() asserts now.
    _prev = None
    while GO["v"]:
        _t0 = time.perf_counter()
        if _prev is not None:
            LAG.append(max(0.0, (_t0 - _prev - POLL_CYCLE) * 1000.0))
        _prev = _t0
        try:
            requests.get(BASE + "/api/v1/health", timeout=20)
        except Exception:
            pass
        LAT.append((time.perf_counter() - _t0) * 1000.0)
        time.sleep(POLL_SLEEP)


def _lagread():
    """The service's own loop-lag block, or None when the build under test publishes no such
    counter. Read from /api/v1/metrics, the same single source of truth this layer already uses
    for worker_cap / peak_concurrency, so the instrument cannot be flattered by the client."""
    try:
        blk = (get("/api/v1/metrics", to=20).json() or {}).get("loop_lag")
    except Exception:
        return None
    return blk if isinstance(blk, dict) and isinstance(blk.get("recent_ms"), list) else None


def _lagspan(before, after):
    """(n_samples_in_window, max_ms, p95_ms, median_ms, n_missing) for the watchdog wakes that
    landed between the two /metrics snapshots, or None when either snapshot lacks the counter.

    The raw list is taken from the AFTER snapshot and filtered by wake index against the sample
    count the BEFORE snapshot reported, so the window is defined by the service's own monotone
    counter rather than by wall-clock alignment between two processes. `n_missing` is the gap
    evidence: the ring is window-length long, and every measured window here is far shorter than
    that, so a nonzero gap would mean the buffer wrapped under a window this layer did not expect
    and would quietly drop the oldest (possibly worst) samples."""
    if not before or not after:
        return None
    try:
        _lo, _hi = int(before["samples"]), int(after["samples"])
        _first = int(after["recent_first_index"])
        _vals = dict(enumerate([float(v) for v in after["recent_ms"]], start=_first))
    except Exception:
        return None
    _idx = range(max(_lo, 0), _hi)
    got = [_vals[i] for i in _idx if i in _vals]
    if not got:
        return None
    return len(got), max(got), pct(got, .95), pct(got, .5), len(list(_idx)) - len(got), sorted(got)


def _lagexcess(span, unit):
    """(n_wakes, mean_excess_ratio, p95_excess_ratio) for one measured window, or None.

    The SERVICE's own watchdog lateness inside the window, with this run's quiet-loop floor taken
    off every sample (clamped at zero) and then divided by that window's UNIT OF WORK. Two
    statistics are returned because one sample count does not make both valid, and this layer has
    already been burned by the difference:

      * the MEAN is defined and stable at any n, so it is the leg that is always asserted. On a
        0.3 s snapshot window the watchdog only lands ~7 wakes, and a p95 over 7 samples is an
        order statistic; the mean over 7 samples is still the average lateness per wake.
      * the P95 is the sharpest reading of "how bad can one wake be held" and is asserted only
        once the window holds at least MIN_SAMPLES wakes -- the same gate the canary tail leg uses
        (L6-B1: "the tail leg is only judged once at least 25 polls landed inside the operation").
        Measured on run13: one 12-client wave closed in 0.33 s, the window held n=5 wakes, so the
        clean product was judged on a SINGLE late wake (max 90.6 ms, 0.46x) and stake m3 on a
        single late wake too (162.0 ms, 1.21x). The ordering was right; the quantity was luck.
        L6-B4 now floors its window at B4_MIN_WINDOW_S so that check's p95 is a real quantile;
        the shorter windows (B1 snapshot, B2 package) keep only the mean leg.

    The floor subtracted per sample is the calibration window's P95, not its median -- the
    conservative end: it is at least as late as 95% of the wakes a silent loop produces, so the
    excess under-counts real lateness slightly rather than inventing it. The bias runs one way
    (towards passing the product, never against it), which is the direction this layer chooses
    when a statistic has to be built from a platform floor it cannot remove."""
    if not span:
        return None
    _fl = IDLE_LAG_P95 if math.isfinite(IDLE_LAG_P95) else 0.0
    _ex = [max(0.0, v - _fl) for v in span[5]]
    if not _ex or unit is None:
        return None
    _u = max(unit, .01)
    return len(_ex), (sum(_ex) / len(_ex)) / _u, pct(_ex, .95) / _u


def measure(work):
    """Run `work` while polling the canary: (wall, p50, p95, n_polls, (lag_p50, lag_p95),
    result, span).

    n_polls is part of the contract, not decoration: at n=16 a p95 is the 15th of 16 values,
    so a single OS scheduling hiccup is the tail. The starvation legs that divide by the
    median are only meaningful once enough polls landed inside the operation -- see
    MIN_SAMPLES and _starve().

    `lag` is the CLIENT-side cadence excess, kept only as a printed reference since stake m3
    showed it measures this process's own thread pool under a concurrent burst.

    `span` is the SERVICE-side loop-lag window (see _lagspan): the two /metrics reads bracket
    the work, so the samples it counts were taken by a task sleeping on the loop under test."""
    del LAT[:]
    del LAG[:]
    _before = _lagread()
    GO["v"] = True
    th = threading.Thread(target=_prober)
    th.start()
    _s = time.perf_counter()
    out = work()
    wall = time.perf_counter() - _s
    GO["v"] = False
    th.join()
    _n = len(LAT)
    _after = _lagread()
    return wall, pct(LAT, .5), pct(LAT, .95), _n, (pct(LAG, .5), pct(LAG, .95)), out, _lagspan(_before, _after)


def _tree_rss():
    import psutil
    p = psutil.Process(proc.pid)
    return sum(psutil.Process(x.pid).memory_info().rss for x in [p] + p.children(recursive=True)) / 1e6


def _starve(wall, p50, p95, idle50, n=None, lag=None, span=None, unit=None, unit_note="",
            tail=True, wallfrac=True, lagleg=True):
    """Evaluate the same-run starvation legs and return (ok, detail). Every number in the
    detail line is derived from THIS run, so a machine that is simply slow overall cannot
    turn the check red, and a machine that is simply fast cannot hide a long block.

    TWO of the three legs this helper once asserted on have been retired by measurement, and
    both retirements are the SAME fallacy the top of this file names denominator collapse:

    (1) the tail leg (p95/p50 of the canary inside the window) is now sample-size gated --
        see MIN_SAMPLES below;
    (2) the sustain leg (p50_during / this run's idle p50) is no longer asserted AT ALL. It
        was the designed catcher for "many medium blocks" (stake m4), and the frozen logs
        show it cannot tell clean code from defect: the clean readings on the very same leg
        are 0.9 / 1.0 / 1.9 / 0.9 / 1.2 (L6-B1, runs 5-8 + 10), 0.9 / 1.0 / 1.3 / 1.4 / 1.4 /
        1.6 / 2.9 (L6-B2) and 1.3 / 1.5 / 1.6 / 1.6 / 1.7 / 1.8 (L6-B3), while the defect it
        exists for reads 4.2x -- i.e. the ceiling (3.0) sits between 2.9 and 4.2, one poll
        wide. Worse, it went RED on the correct product at 3.5x in run9 with nothing starved
        (canary p50 15.4 ms during an offloaded 0.76 s snapshot). The cause is the numerator,
        not only the denominator: the ABSOLUTE level of a /health poll during any heavy window
        is noise-dominated (4.1 / 4.9 / 5.3 / 5.4 / 6.2 / 6.3 / 6.4 / 7.7 / 15.4 ms for the
        same correct snapshot across nine runs), so dividing that by an idle median that itself
        ranges 3.9-6.8 ms produces a number with no defect content in it. What replaces it is the
        ASSERTED loop-lag leg below: the wake lateness the SERVICE recorded for its own event loop
        inside this window, floor-subtracted and divided by this window's unit of work, taken as
        BOTH a mean (asserted at any sample count) and a p95 (asserted only once the window holds
        MIN_SAMPLES wakes -- see _lagexcess). It is a counter the product keeps about the product
        (the peak_concurrency family), it cannot be saturated by
        this process's thread pool, and it was added by failing RED first -- a build with no
        loop_lag counter is RED here, not silently exempt.

    What the tail leg learned, kept for the record: three collateral reds on code with nothing
    starved -- the same statistic reading 20.5x while an unmutated 120 MB snapshot copied in
    0.25 s, 14.1x at wall 0.13 s and 14.7x at wall 0.40 s, against 1.3-8.0x for the same leg on
    slower legs of the same product. A short operation lands few polls, and at n<MIN_SAMPLES a
    p95 IS the maximum: one Windows scheduling hiccup moves it. PHASE 58 CLOSED THE OTHER HALF OF
    THAT TRAP: the same flip happens ABOVE the sample floor. The L6-B2 readings that come from a run
    which REACHED ITS VERDICT (p58_b2_population.py enumerates every L6 log on disk by glob, splits
    the readings by whether the log carries an `L6 TOTAL` line, and lists the non-adjudicating ones
    separately rather than mixing them in -- a snapshot at the time of writing: NINE clean readings,
    FOUR red-leg readings, SEVEN stake readings, TWENTY adjudicating readings in all; re-read the
    receipt instead of trusting these counts, p58_b2_citation_check.py being the instrument that
    re-derives them from it) -- inside the clean population they span 1.2-2.9x with their worst poll
    at 0.01-0.02 of their own window, and the only reading past TAIL_MAX among the adjudicating set
    is 20.1x (p50 6.6 ms,
    p95 132.1 ms, n=30 -- past MIN_SAMPLES, so the gate above did not and could not save it), a
    stake leg whose same-mutation sibling logged 1.4x rather than a healthy run that caught a bad
    hour on the machine. The rule now asserted
    (see TAIL_MATERIAL_WALL_FRAC) is joint: an over-ceiling tail is charged only when this window's
    worst poll is also material against the operation's own wall clock (> 0.25). Both defect
    readings stay charged (129.6x at wallfrac 1.00, stake m1 43.9x at 0.99 -- see the evidence
    limit recorded on the constant: those two raw lines are no longer on disk, their staying
    charged is proven by the counterfactual arms and the live stake); what is exempted is a stake
    leg whose worst poll stayed under a quarter of its own window, and it prints why. Defects do not
    hide in this corner -- the block that starves a console makes the operation slow, which is the
    many-polls case (the un-offloaded snapshot measures 50 polls at the same pinned 43.9x, the one
    the evidence limit says has no raw detail line left on disk). When the tail
    is skipped for sample size, the wall-fraction leg is tightened instead: a poll longer than a
    quarter of the whole operation's wall clock is a stall no poll count can excuse (the same
    TAIL_MATERIAL_WALL_FRAC constant, single-sourced). The loop-lag
    leg meets the SAME small-sample problem from its own side and is gated the same way: its sample
    count is the service's own wake count over the window (a 0.3 s window holds ~7 wakes at the
    50 ms heartbeat), so below MIN_SAMPLES wakes only the MEAN leg is asserted -- the mean of 7
    latenesses is still the average lateness, while the p95 of 7 is one Windows hiccup. Run13
    measured this trap before it was gated: judged on a single wake the clean product read 0.46x
    and stake m3 read 1.21x, a correct ordering with no distribution behind it. The two facts are
    recorded on B4, which floors its window at B4_MIN_WINDOW_S so that its p95 IS asserted, and on
    the shorter windows (B1, B2), which assert the mean."""
    tail_x = p95 / max(p50, .01)
    sustain_x = p50 / max(idle50, .01)
    wall_f = p95 / max(wall * 1000.0, .01)
    sparse = (n is not None and n < MIN_SAMPLES
              and p50 > P50_FLOOR and tail)
    # Phase 58: an over-ceiling tail is only a failure when it is MATERIAL to this window.
    # See TAIL_MATERIAL_WALL_FRAC for the population of verdict-reaching readings that pinned 0.25 and for
    # what this term gives up (sub-quarter blocks) and what still catches them (loop-lag leg).
    tail_material = wall_f > TAIL_MATERIAL_WALL_FRAC
    tail_exempt = (tail_x > TAIL_MAX) and (not tail_material)
    tail_ok = (not tail) or sparse or tail_x <= TAIL_MAX or not tail_material
    wall_ok = ((not wallfrac) or
               wall_f <= (TAIL_MATERIAL_WALL_FRAC if sparse else WALL_FRAC))
    if not lagleg:
        lag_ok, _ll = True, "loop-lag leg not part of this check"
    elif not span:
        lag_ok, _ll = False, ("RED -- the service published no loop_lag counter, so its own loop "
                              "responsiveness cannot be audited")
    else:
        _lx = _lagexcess(span, unit)
        _floor = IDLE_LAG_P95 if math.isfinite(IDLE_LAG_P95) else 0.0
        if _lx is None:
            lag_ok = False
            _ll = ("RED -- the window produced no unit of work to normalise the loop-lag samples "
                   "against (%s); n=%d wakes, floor %.1fms" % (unit_note, span[0], _floor))
        else:
            _mean_ok = _lx[1] <= LOOP_LAG_MEAN_CEIL
            _p95_ok = (_lx[0] < MIN_SAMPLES) or (_lx[2] <= LOOP_LAG_P95_CEIL)
            lag_ok = _mean_ok and _p95_ok
            _ll = ("loop-lag(service) n=%d max=%.1f p95=%.1fms floor=%.1fms -> mean %.2fx (allow "
                   "%.2f, %s), p95 %.2fx (allow %.2f, %s) of this window's %s (%.0fms)%s"
                   % (span[0], span[1], span[2], _floor, _lx[1], LOOP_LAG_MEAN_CEIL,
                      "ok" if _mean_ok else "RED", _lx[2], LOOP_LAG_P95_CEIL,
                      "skip n<%d" % MIN_SAMPLES if _lx[0] < MIN_SAMPLES
                      else ("ok" if _p95_ok else "RED"),
                      unit_note, unit,
                      " | %d wake(s) not in the ring" % span[4] if span[4] else ""))
    ok = tail_ok and wall_ok and lag_ok
    _ex = ""
    if lag and unit and math.isfinite(lag[1]):
        _ex = (" | client-side excess p50=%.1f p95=%.1fms = %.2fx of the unit (printed only: "
               "saturates on this process's own threads)" % (lag[0], lag[1], lag[1] / unit))
    return ok, ("canary p50=%.1fms p95=%.1fms n=%s | tail p95/p50=%.1fx(allow %.0f, %s) "
                "wallfrac p95/wall=%.2f(allow %.2f, %s)%s | %s "
                "| sustain p50/idle_p50=%.1fx reported only (leg retired, see text) "
                "| idle p50=%.1f p95=%.1f, idle-ratio %.1fx reported only"
                % (p50, p95, n, tail_x, TAIL_MAX,
                   "skip n<%d" % MIN_SAMPLES if sparse else
                   ("ok" if tail_x <= TAIL_MAX else
                    ("RED" if tail_material else
                     "not material: p95 is %.2f of this window's wall <= %.2f"
                     % (wall_f, TAIL_MATERIAL_WALL_FRAC))),
                   wall_f, TAIL_MATERIAL_WALL_FRAC if sparse else WALL_FRAC,
                   "ok" if wall_ok else "RED", _ex, _ll,
                   sustain_x, idle50, IDLE95, p95 / max(IDLE95, .01)))


_body_done = False   # set on the last line of the load set; the teardown prints the verdict with
                     # this flag, so a load set that RAISED can never be printed as an adjudication
try:
    if READY is None:
        check("L6 harness: source service answers the health contract", False, "timeout >90s")
        raise RuntimeError("service never became ready")
    print("[HARNESS] source ready=%.2fs port=%d data_root=%s" % (READY, PORT, a(DATA)))

    def post(p, j=None, to=900):
        return requests.post(BASE + p, json=j, timeout=to)

    def get(p, to=300):
        return requests.get(BASE + p, timeout=to)

    def batch_metrics():
        """The service-wide batch worker budget as the service itself publishes it."""
        try:
            return (get("/api/v1/metrics").json() or {}).get("batch") or {}
        except Exception:
            return {}

    START_RSS = None
    try:
        START_RSS = _tree_rss()
    except Exception as e:
        skip("L6-D3 RSS growth across the load set", "psutil unavailable at start: %s" % a(e))

    check("L6-D1 source cold start: process spawn -> health contract answered within 10s "
          "(P52 baseline 2.3-2.8s; a loose ceiling, because this is a travel-safe budget and "
          "not a performance claim)",
          READY <= 10.0, "ready=%.2fs" % READY)

    # ---------- fixtures sized like real work ----------
    small = FIX / "small.png"
    Image.new("RGB", (900, 700), (248, 248, 248)).save(small)
    big = FIX / "big.png"
    im = Image.new("RGB", (3200, 2000), (243, 243, 243))
    px = im.load()
    for _x in range(0, 3200, 4):
        for _y in range(0, 2000, 4):
            px[_x, _y] = (_x % 250, _y % 250, 130)
    im.save(big)
    blob = FIX / "doc.psd"
    with open(blob, "wb") as fh:
        _blk = bytes(range(256)) * 16384            # 4 MB deterministic block
        for _ in range(30):
            fh.write(_blk)                          # 120 MB -- the size a snapshot carries
    print("[FIXTURES] small=%dB big=%dB blob=%dB" % (small.stat().st_size,
                                                     big.stat().st_size, blob.stat().st_size))

    # 60 samples, not 20: a p95 over 20 samples IS the maximum, so B6's start/end
    # comparison would be one OS hiccup wide rather than a degradation signal. Same n on
    # both ends keeps the ratio honest.
    idle = []
    idle_srv = []
    for _ in range(IDLE_SAMPLES):
        _s = time.perf_counter()
        _hb = get("/api/v1/health", to=10)
        idle.append((time.perf_counter() - _s) * 1000.0)
        try:
            idle_srv.append(float(_hb.headers.get("X-Process-Time")) * 1000.0)
        except (TypeError, ValueError):
            pass
    IDLE50, IDLE95 = pct(idle, .5), pct(idle, .95)
    print("[BASELINE] idle canary p50=%.1fms p95=%.1fms n=%d | server-side p50=%.3fms "
          "p95=%.3fms n=%d/%d" % (IDLE50, IDLE95, len(idle), pct(idle_srv, .5),
                                  pct(idle_srv, .95), len(idle_srv), IDLE_SAMPLES))

    # ---------- the instrument itself, before anything divides by it ----------
    _blk0, _blk1 = _lagread(), None
    if _blk0:
        time.sleep(1.0)
        _blk1 = _lagread()
    _live = bool(_blk0 and _blk1)
    _grown = (int(_blk1["samples"]) - int(_blk0["samples"])) if _live else 0
    _iv = float(_blk1.get("interval_ms") or 0.0) if _live else 0.0
    _expect = (1000.0 / _iv) if _iv > 0 else 0.0
    check("L6-B0 the service's loop-lag watchdog is a LIVE instrument, not a field that happens "
          "to be present: /api/v1/metrics must publish loop_lag with a positive interval and a "
          "monotone sample count, and across one second of quiet the count must grow by at least "
          "half the wakes its own published interval implies. Four of this layer's checks (B1, "
          "B2, B3, B4) now divide by this counter's window, so a counter pinned at 0.0 would make "
          "all four pass by being empty -- the stub-must-live-in-the-process-under-test family "
          "this project has been burned by before",
          _live and _iv > 0 and _expect > 0 and _grown >= 0.5 * _expect,
          "interval=%.1fms samples %s->%s (+%d, expected ~%.0f, floor %.0f) | window=%s max=%.1fms "
          "%s" % (_iv, _blk0.get("samples") if _blk0 else "absent",
                  _blk1.get("samples") if _blk1 else "absent", _grown, _expect,
                  0.5 * _expect, _blk1.get("window") if _live else "absent",
                  _blk1.get("max_ms") or 0.0 if _live else 0.0,
                  "" if _live else "| the build under test publishes no loop_lag counter"))

    # The one second of silence that just proved the counter is alive is also the only moment in
    # the session with NO traffic of any kind, so it is used as the instrument's own floor: a loop
    # with nothing to do still wakes late by the platform's timer granularity (this box measures
    # 10.9-13.7 ms against a 50 ms heartbeat, which is Windows' 15.6 ms clock tick, not the
    # product holding the loop). Every asserted loop-lag ratio below therefore subtracts THIS
    # run's own floor before dividing by the unit of work -- the alternative is a criterion that
    # measures the operating system's clock. It is subtracted, not divided, because it is an
    # additive delay; and it is read from the same counter in the same process, so it carries no
    # transport floor and no client-side thread contention.
    _cal = _lagspan(_blk0, _blk1)
    IDLE_LAG_P50 = _cal[3] if _cal else float("nan")
    IDLE_LAG_P95 = _cal[2] if _cal else float("nan")
    print("[BASELINE] service loop-lag floor over 1s of silence: n=%s p50=%.1f p95=%.1fms"
          % (_cal[0] if _cal else "absent", IDLE_LAG_P50, IDLE_LAG_P95))

    # ---------- B1 big snapshot ----------
    _w, _p50, _p95, _n, _lag, _sid, _sp = measure(lambda: post(
        "/api/v1/recovery/snapshot", {"document_path": str(blob)}).json().get("id"))
    _ok, _det = _starve(_w, _p50, _p95, IDLE50, n=_n, lag=_lag, span=_sp,
                        unit=_w * 1000.0, unit_note="snapshot's own wall")
    check("L6-B1 a 120 MB snapshot must not starve the service, judged only by this run's own "
          "numbers: one /health poll may not take %.0fx the median poll of the same operation "
          "and may not be more than %.0f%% of the snapshot's own wall clock. The tail leg is "
          "only judged once at least %d polls landed inside the operation -- below that a p95 "
          "IS the maximum of a handful of samples, so the wall-fraction ceiling tightens to 25%% "
          "instead and still catches a stall that takes a quarter of the request. Measured "
          "reason, from the frozen logs: this same statistic read 20.5x while an UNMUTATED "
          "snapshot copied in 0.25s (4 polls) and 14.7x at 0.40s, against 1.3-8.0x for the same "
          "leg on the slower legs of the same product -- three collateral reds on code with "
          "nothing starved. What a real violation looks like was measured too: in the RED "
          "baseline run (p52_l6_run1.out) shutil.copyfile of 120 MB + a streaming sha256 sat "
          "directly inside async def create_snapshot and the poll ran to 1375.4 ms over 50 polls "
          "-- tail 117.6x its own median, 98%% of the request's wall -- and mutation stake m1 "
          "(the same copy put back on the loop by deleting one to_thread) reproduced it at tail "
          "43.9x / wallfrac 0.99. THE THIRD LEG THIS CHECK USED TO ASSERT (canary median vs this "
          "run's idle median) IS RETIRED, and it is retired for the same reason the idle-P95 "
          "ratio was: on nine runs of the CORRECT product the snapshot window's own canary median "
          "measured 4.1 / 4.9 / 5.3 / 5.4 / 5.4 / 6.2 / 6.3 / 6.4 / 7.7 / 15.4 ms, so the "
          "numerator is noise before the denominator ever gets a turn -- run9 went RED at 3.5x "
          "on p50=15.4/idle=4.4 with nothing starved, and the defect the leg existed for (stake "
          "m4) reads 4.2x, i.e. the ceiling sat one poll above normal machine variation. What "
          "replaces it is ASSERTED here: the wake lateness the service recorded for its own event "
          "loop inside this window, less this run's quiet-loop floor and divided by this window's "
          "unit of work -- asserted on its MEAN (allow %.2fx of it) and, once the window holds at "
          "least %d watchdog wakes, on its p95 (allow %.2fx); a short window like this snapshot's "
          "judges the mean only, for the reason measured on B4 "
          "-- a counter the product keeps about the product, unreachable by this client's thread "
          "pool, which is why it replaces a client-side statistic that stake m3 measured to be "
          "identical on product and defect (175.7 ms clean vs 175.1 ms blocked over the same "
          "12-client burst). A build that "
          "publishes no such counter is RED, not exempt. The idle RATIO is "
          "reported, never asserted: that same p95 was 41.9x idle in run1 and only 5.6x in stake "
          "m1, because a healthy run's idle p95 ranges 4.9-21.8 ms across machines"
          % (TAIL_MAX, WALL_FRAC * 100, MIN_SAMPLES, LOOP_LAG_MEAN_CEIL, MIN_SAMPLES,
             LOOP_LAG_P95_CEIL),
          _sid is not None and _ok,
          "wall=%.2fs id=%s | %s" % (_w, a(_sid), _det))

    # ---------- B2 delivery package ----------
    _w, _p50, _p95, _n, _lag, _pid, _sp = measure(lambda: post("/api/v1/delivery/package", {
        "project": "l6", "files": {"Source": [str(big), str(blob)],
                                   "Report": [str(small)]}}).json().get("package_id"))
    _ok, _det = _starve(_w, _p50, _p95, IDLE50, n=_n, lag=_lag, span=_sp,
                        unit=_w * 1000.0, unit_note="package's own wall")
    check("L6-B2 a delivery package (copy2 + sha256 + ZIP deflate over 120 MB) must not starve "
          "the service on the same three asserted same-run legs as L6-B1 (tail, wall fraction and "
          "the service's own loop-lag window over this request; the idle-median leg is retired with "
          "the measurements recorded on L6-B1). The RED "
          "baseline run measured 1425.2 ms = tail 129.6x its own median and 100%% of the "
          "request's wall, with the whole copy+hash+deflate inside the coroutine. The clean "
          "population and the Phase 58 joint gate on the tail leg are pinned in the comment on "
          "TAIL_MATERIAL_WALL_FRAC. The population behind it is ENUMERATED, not hand-picked -- "
          "p58_b2_population.py globs every L6 log on disk, proves its own extractor with a "
          "positive control, and splits the readings by whether the run reached its `L6 TOTAL` "
          "line, so treat the numbers quoted here as a point-in-time snapshot of that receipt "
          "rather than a constant: at the time of writing it read NINE clean readings, FOUR "
          "red-leg readings, SEVEN stake readings, TWENTY adjudicating readings in all; inside the "
          "clean population the tail spans 1.2-2.9x with worst-poll fraction <= 0.02 of the "
          "operation's own wall, and the only reading past TAIL_MAX among the adjudicating set is "
          "a stake leg at 20.1x whose worst poll was 0.11 of its window and which the joint gate "
          "therefore exempts, and an "
          "exempted tail prints its wall fraction rather than passing silently" % (),
          _pid is not None and _ok,
          "wall=%.2fs pkg=%s | %s" % (_w, a(_pid), _det))

    # ---------- C1 ingest scaling ----------
    def ingest(k, tag):
        """Ingest k assets -> (median per-add seconds, per-add samples, error string).

        Phase 52 re-derivation. The first edition divided the window's wall clock by k, and
        that mean cannot carry a scaling claim: (a) the first add against a cold service pays
        lazy imports and the first database read, which over a 25-add window is a large slice
        of the mean -- the SAME code path measured per_asset(N=25) at 126.8 / 34.4 / 51.8 /
        29.1 ms across four runs, a 4.4x swing that swamps the 1.26-vs-2.95 clean/defect gap
        the ceiling exists to separate; and (b) the append-only log compacts occasionally, so
        the mean carries a spike that is amortised by design. The median of the per-add times
        is immune to both, and it still grows with corpus size under the defect this check
        exists for (stake m4 rewrites the whole embeddings DB on EVERY add, so every sample,
        median included, pays for it).
        """
        times = []
        for i in range(k):
            f = FIX / ("ing_%s_%03d.png" % (tag, i))
            shutil.copyfile(small, f)
            _s = time.perf_counter()
            r = post("/api/v1/assets", {"file_path": str(f)})
            if r.status_code not in (200, 201):
                return None, times, "http %s %s" % (r.status_code, a(r.text[:120]))
            times.append((time.perf_counter() - _s) * 1000.0)
        return pct(times, .5) / 1000.0, times, "ok"

    c_small, s_small, e1 = ingest(C1_SMALL_N, "a")
    srch_small = post("/api/v1/assets/search", {"query": "package mockup", "top_k": 10}).json()
    n_small = max(srch_small.get("indexed_embeddings") or 0, 1)
    t_small = max(srch_small.get("search_time") or 0.0, 0.0005)
    c_big, s_big, e2 = ingest(C1_BIG_N, "b")
    _slope_small = ((c_small or 0) * 1000.0)
    _slope_big = ((c_big or 0) * 1000.0)
    # the physical quantity behind the ratio: how many milliseconds ONE extra asset in the
    # corpus costs on every subsequent add. Printed, deliberately not asserted -- an absolute
    # ms/asset is machine-speed talk, and this layer judges on same-run relative shape.
    _marg = ((_slope_big - _slope_small) / max(C1_BIG_N, 1)) if (c_small and c_big) else float("nan")
    check("L6-C1 ingest scaling: the MEDIAN cost of one add at a %d-asset corpus stays within "
          "%.2fx the median cost of one add at a %d-asset corpus. This is the SECOND EDITION of "
          "the check and the widening is measurement, not a goalpost moved to make a stake red: "
          "the pre-Phase-52 product rewrote the whole embeddings DB on every add (add_asset ended "
          "in save_databases(), N x 512 floats at indent=2) and the spike saw 22.8 ms -> 395 ms "
          "per asset (~17x over 25/100/250 buckets, 0.57 s / 13.82 s / 98.81 s); after the "
          "append-only log + compaction the MEAN-per-add statistic swung 0.31 / 1.09 / 1.23 / "
          "1.19 / 1.26 / 1.87 across six runs (the 1.87 failed a correct product), which is why "
          "the statistic became the MEDIAN. The median still failed, and the cost-curve spike "
          "(p52_c1_spike.out) says why: the CORRECT product is itself O(N) per add -- it rewrites "
          "the assets snapshot every add, measured 17.3 / 26.6 / 35.5 / 46.8 ms at corpus "
          "25 / 100 / 250 / 500 -- so a growth ratio over the first edition's short span "
          "(25 -> 175) compares two linear curves while the fixed per-add cost still dominates "
          "and cannot see the difference between their SLOPES. Measured on that span, clean read "
          "1.09 / 1.20 / 1.21 / 1.25 / 1.46 across five runs while stake m4 -- the whole-DB "
          "rewrite put back on the hot path -- read 1.66, i.e. a ceiling at 1.75 sat ON the "
          "defect and a correct product already reached 1.46: the L6-B4 first-edition failure "
          "mode again. The spike's marginal costs are the reason a longer span fixes it: 0.062 ms "
          "per asset in corpus for the correct product against 0.50 ms for the defect (8x the "
          "slope), so over 25 -> 250 the same ratio statistic reads clean 2.05x against defect "
          "6.48x -- a 3.2x separation bought with roughly six extra seconds of clean runtime. THE "
          "CEILING IS PINNED on that widened statistic from both measured populations, never by "
          "lowering it to fit a failing clean run: seven in-suite clean runs (run18-run24) read "
          "1.15 / 1.31 / 1.34 / 0.89 / 1.32 / 1.36 / 1.43 -- that 0.89 is the dilution law in "
          "plain sight, a transient "
          "made the 25-asset window cost MORE than the 250-asset one, so this ratio can fall under "
          "one and no check here pretends otherwise -- while the same ingest path measured with no "
          "prior legs on the service (p52_c1_spike.out, i.e. a much smaller fixed per-add cost) "
          "reads 2.05 over the identical span. 2.05, not 1.43, is what the pin has to clear, "
          "because a lighter machine is a condition and not a hypothetical. Stake m4 reads 3.57 / "
          "3.58 / 3.68 in-suite and 6.48 unloaded, so its floor is 3.57. The geometric midpoint of "
          "those binding extremes is 2.71: the pin (the allowed value printed above) sits 24%% "
          "above the highest clean reading in any condition measured and 32%% below the lowest "
          "defect reading -- against the first edition's 5%% margin over the defect and a correct "
          "product already reaching within 17%% of its ceiling. The printed marginal ms-per-asset "
          "is the physical quantity behind it (0.02-0.06 clean, 0.42-0.50 on the defect, an 8-10x "
          "slope gap) and stays UNASSERTED on purpose: an absolute ms/asset is machine-speed talk, "
          "and this layer judges on same-run relative shape."
          % (n_small + C1_BIG_N, C1_MEDIAN_CEIL, n_small),
          bool(c_small and c_big) and c_big <= C1_MEDIAN_CEIL * c_small,
          "median per add: N=%d %.1fms -> N=%d %.1fms = %.2fx (allow %.2f) | marginal %.3f ms per "
          "asset in corpus (printed, not asserted) | mean %.1f -> %.1fms = %.2fx | worst add in big "
          "window %.1fms | p95 %.1f -> %.1fms | %s %s"
          % (n_small, (c_small or 0) * 1000, n_small + C1_BIG_N, (c_big or 0) * 1000,
             (c_big / c_small) if c_small and c_big else float("nan"), C1_MEDIAN_CEIL, _marg,
             (sum(s_small) / len(s_small)) if s_small else float("nan"),
             (sum(s_big) / len(s_big)) if s_big else float("nan"),
             ((sum(s_big) / len(s_big)) / max(sum(s_small) / len(s_small), .01))
             if s_small and s_big else float("nan"),
             max(s_big) if s_big else float("nan"),
             pct(s_small, .95), pct(s_big, .95), a(e1), a(e2)))

    # ---------- B3 import burst of large images ----------
    burst = []
    for i in range(20):
        f = FIX / ("burst_%03d.png" % i)
        shutil.copyfile(big, f)
        burst.append(str(f))
    _w, _p50, _p95, _n, _lag, _, _sp = measure(lambda: [post("/api/v1/assets", {"file_path": fp})
                                                        for fp in burst])
    _ok, _det = _starve(_w, _p50, _p95, IDLE50, n=_n, lag=_lag, span=_sp,
                        unit=_w * 1000.0 / len(burst), unit_note="per-import span",
                        wallfrac=False)
    check("L6-B3 a burst of large-image imports must not starve the service, judged on the tail "
          "leg (the wall leg is dropped here because 20 sequential HTTP round-trips make the "
          "wall clock a legitimate denominator of its own, so p95/wall can never fire). The RED "
          "baseline run measured 996.5 ms with a median of 15.5 ms -- tail 64.3x -- because PIL "
          "open/convert/thumbnail/save and the per-add database rewrite ran directly inside "
          "async def; clean runs of this burst read tail 2.8-4.3x. WHAT ELSE ASSERTS THIS SHAPE: "
          "the leg that used to catch MANY medium blocks here (canary median vs idle median) is "
          "retired with the nine clean readings printed on L6-B1, so the defect that shape "
          "represents -- mutation stake m4, the whole embeddings DB rewritten by every one of "
          "these 20 adds, which reads tail only 4.3x -- is now pinned by L6-C1, which measures "
          "the MEDIAN COST OF ONE ADD at a grown corpus against one at a small corpus (clean "
          "1.19-1.26x, stake m4 2.95x) and needs no idle baseline at all. ADDED TO THIS CHECK "
          "after those measurements were on disk: the service's own loop-lag window, normalised "
          "by the per-import span, is now the asserted third leg, because the client-side excess "
          "printed here (and asserted on the fourth edition of L6-B4) turned out to measure this "
          "process's own thread pool rather than the service's loop"
          , _ok, "wall=%.2fs | %s" % (_w, _det))

    # ---------- C2 retrieval at-or-below linear + B4 loop-starvation ----------
    # WHY B4 REPEATS: interference only ever ADDS latency, so the least-contaminated of REPEATS
    # identical legs is the honestest estimate of what the product does -- for the service's own
    # watchdog too, because the /metrics reads that bracket each leg are made by this process.
    # WHY THE BURST IS CONCURRENT AND NOT SEQUENTIAL (kept from the fourth form, changed by
    # stake m3): with 15 SEQUENTIAL requests the loop has a free gap between every pair of kernels,
    # so one 22 ms cosine pass either shape leaves the canary at roughly the same place -- the
    # frozen logs show it exactly: clean run10 read canary p50=10.1 p95=44.7 ms, stake m3 (that
    # same pass put back on the loop) read p50=27.2 p95=78.3 ms. A 1.75x gap is not a criterion, it
    # is the transport floor shrugging. Concurrency is the load shape that gives the PROPERTY
    # dynamic range: B4_CLIENTS searches fired together, so a loop that holds each handler to
    # completion must serve them one after another and hold its own timers behind K kernels, while
    # a loop that handed the work to a worker thread keeps answering them. WHAT MOVED in the fifth
    # form is the instrument, not the shape: the excess is read from the service's own loop.
    # WHY THE BURST IS SUSTAINED (run13, measured, not assumed): one wave closed in 0.33 s on this
    # box, which left only n=5 watchdog wakes inside the window, and the check then judged a p95 of
    # five samples -- i.e. one unlucky late wake -- at 0.46x for the correct product and 1.21x for
    # stake m3. The shape (always B4_CLIENTS in flight) is untouched; the window is simply floored
    # at B4_MIN_WINDOW_S so the asserted p95 is a quantile over at least MIN_SAMPLES wakes.
    def _b4_burst():
        """Waves of B4_CLIENTS concurrent searches until the window reaches B4_MIN_WINDOW_S.

        Returns every response collected, so the unit of work (the median search_time of one
        scoring pass) is the median over the whole sustained burst, not over one wave."""
        _res, _wave, _t0 = [], 0, time.perf_counter()
        while _wave < B4_MAX_WAVES:
            _wave += 1
            _out = {}
            _ths = []

            def _one(_i):
                _out[_i] = post("/api/v1/assets/search",
                                {"query": "package mockup %d" % _i, "top_k": 10}).json()

            for _i in range(B4_CLIENTS):
                _t = threading.Thread(target=_one, args=(_i,))
                _t.start()
                _ths.append(_t)
            for _t in _ths:
                _t.join()
            _res.extend(_out[_i] for _i in sorted(_out))
            if time.perf_counter() - _t0 >= B4_MIN_WINDOW_S:
                break
        return _res

    _legs = [measure(lambda: _b4_burst()) for _r in range(REPEATS)]
    _w, _p50, _p95, _n, _lag, _sr, _sp = _legs[0]
    n_big = max((_sr[0].get("indexed_embeddings") if _sr else 0) or n_small, n_small)
    t_big = min([max(x.get("search_time") or 0.0, 0.0005) for x in (_sr or [{}])] or [t_small])

    def _qlat(leg):
        """(one unit of work in ms, canary-median/query, lag-p95/query, lag-p50/query).

        leg[4] is the (lag_p50, lag_p95) pair of the CLIENT-side cadence excess. Neither of the
        two ratios this returns is asserted any more -- leg[2] is printed as the historical
        statistic and leg[3] as its median sibling; see leg[6] / _lagx for what replaced them and
        why."""
        _qs = sorted((x.get("search_time") or 0.0) for x in (leg[5] or []))
        _q = 1000.0 * _qs[len(_qs) // 2] if _qs else 0.0
        return (_q, (leg[1] / _q if _q > 0 else float("inf")),
                (leg[4][1] / _q if _q > 0 else float("inf")),
                (leg[4][0] / _q if _q > 0 else float("inf")))

    def _lagx(leg):
        """(span, mean_excess_ratio, p95_excess_ratio) for the SERVICE-side leg.

        The wake lateness the service's own watchdog recorded inside this window, LESS this run's
        quiet-loop floor (the platform's timer granularity, measured over the 1 s of silence that
        proved the counter alive), divided by one scoring pass -- see _lagexcess for why BOTH a
        mean and a p95 are returned. None for the ratios when the build publishes no counter at
        all, so an unauditable loop is RED rather than exempt."""
        _sn = leg[6]
        _lx = _lagexcess(_sn, _qlat(leg)[0])
        # (span, mean-excess ratio, p95-excess ratio); _lagexcess repeats the wake count as its own
        # first element, which is span[0] already, so only the two ratios are carried forward here.
        return (_sn, None, None) if _lx is None else (_sn, _lx[1], _lx[2])

    _lxs = [_lagx(_l) for _l in _legs]
    _q_ms = _qlat(_legs[0])[0]
    _p50r = sorted(_qlat(_l)[1] for _l in _legs)      # reported only (see the note above)
    # A leg passes when EVERY applicable loop-lag leg of that leg passes: the mean always, the p95
    # only once the window holds MIN_SAMPLES wakes. The check then takes any passing leg -- the
    # same least-contaminated-of-REPEATS convention documented above.
    _legok = [(_x[1] is not None and _x[1] <= LOOP_LAG_MEAN_CEIL
               and (_x[0][0] < MIN_SAMPLES or _x[2] <= LOOP_LAG_P95_CEIL)) for _x in _lxs]
    _b4m = sorted(_qlat(_l)[2] for _l in _legs)       # reported: old client-side excess / unit
    # the leg reported as "judged" is the least-contaminated one on the mean (see REPEATS above);
    # its p95 is printed beside it, together with every leg's own wake count, so a reader can see
    # the distribution the statistic was taken over rather than being handed a single number.
    _ji = min(range(len(_lxs)), key=lambda i: (_lxs[i][1] if _lxs[i][1] is not None else 1e9))
    _jn = _lxs[_ji][0][0] if _lxs[_ji][0] else 0
    _jm = _lxs[_ji][1]
    _jp = _lxs[_ji][2]
    _snote = ("" if _sp else " | the service published NO loop_lag counter, so its own loop "
                             "responsiveness is unauditable -- RED by definition, not exempt")
    # C2 is a SCALING claim, so it is measured on one sequential request of its own: a
    # concurrency-amplified burst is the wrong place to read what one pass costs.
    _c2 = post("/api/v1/assets/search", {"query": "package mockup", "top_k": 10}).json()
    n_big = max((_c2.get("indexed_embeddings") or 0), n_small)
    t_big = max(_c2.get("search_time") or 0.0, 0.0005)
    check("L6-C2 retrieval cost stays at-or-below linear in corpus size: the server's own "
          "search_time at N=%d must be within 1.5x the growth factor times search_time at N=%d "
          "(brute-force cosine IS linear -- this catches a super-linear regression such as "
          "re-scoring from disk per query; the linear cost itself is the documented ceiling of "
          "a non-indexed engine)" % (n_big, n_small),
          t_big <= 1.5 * (n_big / n_small) * t_small,
          "N %d->%d  search_time %.4fs -> %.4fs (growth %.1fx, allowed %.1fx)"
          % (n_small, n_big, t_small, t_big, t_big / t_small, 1.5 * (n_big / n_small)))
    check("L6-B4 %d concurrent searches over a grown corpus must not block the service's own event "
          "loop by a meaningful fraction of one scoring pass: the wake lateness the "
          "SERVICE recorded for its loop inside the burst window, less this run's quiet-loop floor, "
          "stays within %.2fx of the median "
          "search_time this run measured for one scoring pass on its MEAN and within %.2fx on its "
          "p95 (the p95 leg is judged only once the window holds %d wakes -- see _lagexcess), "
          "taking the least contaminated of %d identical legs. THIS IS THE FIFTH FORM OF THIS "
          "CHECK AND EVERY FORM WAS CHANGED BY "
          "MEASUREMENT, NEVER BY A FAILING CLEAN RUN: (1) p95_during/query with a 2.0 ceiling, "
          "retired because defect m3 read 1.79x -- the ceiling sat on the defect; (2) MEDIAN "
          "p50_during/query at 0.70 best-of-3, retired when stake m3 came back GREEN on it (legs "
          "0.34 / 0.52 / 1.07, best 0.34) -- the round-trip median was the wrong quantity; (3) the "
          "cadence EXCESS of a SEQUENTIAL burst, retired before pinning its ceiling because the "
          "dynamic range was not there to measure: clean run10 read canary p50=10.1 / p95=44.7 ms "
          "against stake m3's p50=27.2 / p95=78.3 ms, a 1.75x gap between the product and the "
          "defect, which is the transport floor shrugging, not a criterion; (4) the same cadence "
          "excess over a CONCURRENT burst, retired by stake m3 a second time because the NUMERATOR "
          "turned out to have no defect content in it at all -- clean run11 read lag p95=175.7 ms "
          "and the loop-blocking defect read p95=175.1 ms over the same 12-client burst, i.e. what "
          "was getting late was this suite's own thread pool, not the service's loop, and the only "
          "separation left came from the denominator (75.2 ms vs 46.1 ms per query), which is the "
          "machine talking, not the product. Concurrency stays because it is the load shape that "
          "makes a loop-bound kernel hurt: with %d searches in flight a loop that runs the kernel "
          "itself serves them one after another and holds its own timers behind K kernels, while a "
          "loop that handed the kernel to a worker thread keeps answering them. What is measured has "
          "moved into the service: a task that sleeps on the event loop and reports how late it "
          "woke, published as /api/v1/metrics loop_lag -- the counters family this layer already "
          "trusts for worker_cap and peak_concurrency, invisible to client-side traffic, and the "
          "leg that replaces the sustain leg retired on L6-B1. A build with no such counter is RED "
          "here. RE-DERIVED INSIDE THE SAME FORM, because run13 showed the window was too short for "
          "the statistic: one 12-client wave closed in 0.33 s and left n=5 watchdog wakes, so the "
          "p95 was one unlucky sample -- clean 0.46x (max 90.6 ms) against stake m3 1.21x (max "
          "162.0 ms). The burst is now SUSTAINED to at least %.1f s (the shape is unchanged, 12 "
          "clients stay in flight and the legs repeat), which puts the p95 on at least MIN_SAMPLES "
          "wakes, and the leg asserts BOTH the mean (any sample count) and that p95. THE MEAN "
          "CEILING IS NOW PINNED on this burst leg, where the statistic has content. Five "
          "sustained runs whose search path is the CORRECT product (clean run15, clean run16, and "
          "stakes m0/m1/m2, whose mutations sit in the snapshot and in the batch grant) judged "
          "0.33 / 0.36 / 0.25 / 0.34 / 0.29 -- the least-contaminated of three legs each time, "
          "worst 0.36 -- while stake m3, the defect this ceiling exists for, judged 0.63 and 0.58 "
          "on its two runs with EVERY leg above the pin in both, and 0.68 (legs 0.85 / 0.68 / "
          "0.76, judged p95 1.44 -- still inside the gross-tail guard, exactly as the paragraph "
          "below predicts of a statistic that discriminates on the mean) on the run taken after "
          "L6-C1's ingest window was widened: the seven runs on the 250-asset corpus judged "
          "0.34 / 0.33 clean and 0.36 / 0.31 / 0.28 / 0.31 / 0.32 for stakes m0/m1/m2/m4/m5, so "
          "neither population moved when the burst leg grew and this pin needed no re-derivation. "
          "The geometric midpoint of those "
          "measured populations is 0.457, so the pin (the allowed value printed above) sits 25%% "
          "clear of the worst healthy judged leg and 29%% clear of the defect's best, and it "
          "catches m3 on every leg, leaving min-of-N nothing to rescue. Unjudged healthy legs do "
          "reach 0.49 (run16) and 0.42 (m1), and the detail line prints all three legs rather than "
          "judging the unlucky ones. The p95 does NOT separate the populations and is pinned loose "
          "on purpose: judged-leg p95 read 0.46-1.00 across the five healthy runs against 1.18 and "
          "1.37 for m3 (an 18%% gap), with unjudged healthy legs up to 1.66, so a ceiling at 1.0 "
          "would sit inside the correct product's own spread -- the run9 mistake wearing a new "
          "costume. It stays a gross-tail guard. The two clock ratios "
          "the earlier forms asserted on are still printed in the detail line, unasserted, along "
          "with the idle-ratio (measured 1.4-37.4x across six runs on unchanged code, which is why "
          "no check here divides by idle)."
          % (B4_CLIENTS, LOOP_LAG_MEAN_CEIL, LOOP_LAG_P95_CEIL, MIN_SAMPLES, REPEATS,
             B4_CLIENTS, B4_MIN_WINDOW_S),
          _q_ms > 0 and any(_legok),
          "wall=%.2fs canary p50=%.1f p95=%.1f per_query=%.1fms | service loop-lag floor=%.1fms; "
          "%d legs n=[%s] mean=[%s] p95=[%s] -> judged leg #%d mean=%sx (allow %.2f), p95=%sx "
          "(allow %.2f, %s); verdicts=[%s] | "
          "client-excess p95 legs=%s -> best %.2fx (printed only) | p50/query best %.2fx, "
          "p95/query %.2fx, idle p95=%.1fms, idle-ratio %.1fx reported only%s"
          % (_w, _p50, _p95, _q_ms, IDLE_LAG_P95, len(_lxs),
             " ".join(str(_x[0][0] if _x[0] else 0) for _x in _lxs),
             " ".join(_fnum(_x[1]) for _x in _lxs), " ".join(_fnum(_x[2]) for _x in _lxs),
             _ji, _fnum(_jm), LOOP_LAG_MEAN_CEIL, _fnum(_jp), LOOP_LAG_P95_CEIL,
             ("skip n<%d" % MIN_SAMPLES if _jn < MIN_SAMPLES else
              ("ok" if (_jp is not None and _jp <= LOOP_LAG_P95_CEIL) else "RED")),
             " ".join("ok" if _o else "RED" for _o in _legok),
             " ".join("%.2f" % _x for _x in _b4m), _b4m[0], _p50r[0],
             _p95 / max(_q_ms, .01), IDLE95, _p95 / max(IDLE95, .01), _snote))

    # ---------- batch: workers sweep ----------
    CAP = batch_metrics().get("worker_cap")     # published by the service, not by this test
    files = [str(small) for _ in range(6)]
    WALL, INFO = {}, {}
    for _wk in (1, 4, 8):
        _w, _p50, _p95, _n, _lag, _j, _ = measure(lambda w_=_wk: post(
            "/api/v1/batch/process", {"file_paths": list(files),
                                      "operation": "remove_background", "workers": w_}).json())
        WALL[_wk], INFO[_wk] = _w, (_j, _p50, _p95)

    # ---- the cross-request leg: three simultaneous batches, one service ----
    # 2 files per client, not the sweep's 6: with the service-wide gate in place every one
    # of these kernels queues behind the others, so the leg's own cost is K * files * 1.5 s.
    _cf = files[:2]

    def _k_batches(k):
        res = {}
        ths = []

        def _run(tag):
            res[tag] = post("/api/v1/batch/process", {"file_paths": list(_cf),
                                                      "operation": "remove_background",
                                                      "workers": 8}).json()
        for _i in range(k):
            t = threading.Thread(target=_run, args=("c%d" % _i,))
            t.start()
            ths.append(t)
        for t in ths:
            t.join()
        return res

    _w3, _p3, _p953, _n3, _lag3, _res3, _sp3 = measure(lambda: _k_batches(3))
    _mb3 = batch_metrics()
    _res3 = _res3 or {}
    _ap3 = [_res3.get(t, {}).get("workers_applied") for t in sorted(_res3)]
    _ok3 = [_res3.get(t, {}).get("success") for t in sorted(_res3)]
    _pf3 = sorted(float(r.get("time") or 0.0)
                  for j in _res3.values() for r in (j.get("results") or []))
    _eff3 = [_res3.get(t, {}).get("parallel_efficiency") for t in sorted(_res3)]
    check("L6-C3a a GIL-bound batch is serialised across the SERVICE, not merely inside one "
          "request: after THREE simultaneous 8-worker batches the service's own monotone "
          "peak_concurrency counter must read exactly 1, in_flight must be back to 0, every "
          "response must report workers_applied=1, and all 3 x 2 files must have been "
          "processed. "
          "Why a counter and not a wall-clock ratio: this check used to assert wall(workers=4) "
          "within 1.15x wall(workers=1), and that was an instrument bug with two failure modes. "
          "(a) All three supported operations are serialised, so the three requests execute "
          "IDENTICALLY and any measured difference is machine noise -- six runs on disk read "
          "0.98x / 1.00x / 3.76x / 1.66x on unchanged code, which red-failed a correct product "
          "twice. (b) Worse, it could not see the real defect: the per-request semaphore let K "
          "clients run K pixel kernels at once while each response still claimed "
          "workers_applied=1 / worker_policy=cpu_gil_bound_serialized. Measured by "
          "outputs/p52_f5_probe.out before the fix: peak_concurrency=3 with 3 x 8 requested, "
          "per-file cost 3.76 s against 1.5 s serialised (2.5x), canary p95 566 ms. After the "
          "service-wide gate the same probe reads peak=1, per-file max 1.33 s, canary p95 252 "
          "ms -- and the queued clients' parallel_efficiency drops below 1.0 instead of everyone "
          "claiming 1.0, which is the contract admitting it waited. Aggregate wall pays ~5% for "
          "that (5.91 s -> 6.20 s for 3 clients x 2 files); the console stops being starved."
          , _mb3.get("peak_concurrency") == 1 and _mb3.get("in_flight") == 0
          and all(v == 1 for v in _ap3) and len(_res3) == 3
          and all(v == len(_cf) for v in _ok3)
          and all(INFO[w][0].get("workers_applied") == 1 for w in (1, 4, 8)),
          "3 simultaneous batches: peak=%s in_flight=%s applied=%s success=%s eff=%s | "
          "per-file max=%.2fs | canary p50=%.1f p95=%.1f | wall(1/4/8 sequential)=%.2f/%.2f/"
          "%.2fs reported only (identical code path, so the ratio is noise: 0.98x/1.00x/"
          "3.76x/1.66x over six runs)"
          % (_mb3.get("peak_concurrency"), _mb3.get("in_flight"), _ap3, _ok3, _eff3,
             (_pf3[-1] if _pf3 else float("nan")), _p3, _p953,
             WALL[1], WALL[4], WALL[8]))
    _j1, _j4 = INFO[1][0], INFO[4][0]
    check("L6-C3b the batch contract must self-report MEASURED efficiency and the budget it "
          "actually got, not just echo the request: every response carries workers_requested "
          "(what the client asked), workers_applied (what the service granted, an int in "
          "[1, cap]) and parallel_efficiency = sum(per-file time)/wall computed at request "
          "time (the pre-Phase-52 spike: per-file time rose 5.95x at workers=8 while wall "
          "barely moved, so 'workers: 8' on its own is a misleading claim). The legacy "
          "'workers' field stays -- L2/L3/L5 all send it, though none of them asserted the "
          "answer until now",
          isinstance(_j4.get("workers_requested"), int) and _j4["workers_requested"] == 4
          and isinstance(_j4.get("workers_applied"), int) and _j4["workers_applied"] >= 1
          and isinstance(_j4.get("parallel_efficiency"), (int, float))
          and isinstance(_j4.get("workers"), int)
          and isinstance(_j4.get("worker_policy"), str)
          and (_j4["worker_policy"] != "cpu_gil_bound_serialized" or _j4["workers_applied"] == 1)
          and isinstance(_j4.get("total_time"), (int, float)),
          a(json.dumps({k: _j4.get(k) for k in ("workers", "workers_requested",
                                                "workers_applied", "worker_policy",
                                                "parallel_efficiency", "total_time", "success",
                                                "failed")}, ensure_ascii=False)))
    _u_ms = 1000.0 * max([float(r.get("time") or 0.0) for r in (_j1.get("results") or [])]
                         or [0.0])
    check("L6-B5 a GIL-bound batch must not push the console further behind than its own work: "
          "canary p95 while 6 files run at workers=1 stays within %.1fx the service's own "
          "slowest per-file cost, measured in this run. The floor is physics, not a defect -- "
          "the flood fill is pure Python and Pillow's C calls hold the GIL, so no threading "
          "scheme removes it; outputs/p52_gil_spike.out measured the one available lever "
          "(sys.setswitchinterval 5ms -> 0.2ms cuts the canary p95 195 ms -> 45 ms and costs "
          "+33%% on every image operation), and V1.0 declines to pay 33%% of its primary "
          "workflow for a background poll. Asserted against the kernel's unit cost rather than "
          "idle for the reason documented on L6-B4. What this DOES pin is the contract: a "
          "GIL-bound operation must come back serialised" % GIL_UNIT_CEIL,
          _u_ms > 0 and INFO[1][2] <= GIL_UNIT_CEIL * _u_ms
          and _j1.get("worker_policy") == "cpu_gil_bound_serialized"
          and _j1.get("workers_applied") == 1,
          "canary p50=%.1f p95=%.1f per_file_max=%.1fms p95/file=%.2fx (idle p95=%.1fms, "
          "idle-ratio %.1fx reported only) policy=%s applied=%s"
          % (INFO[1][1], INFO[1][2], _u_ms, INFO[1][2] / max(_u_ms, .01), IDLE95,
             INFO[1][2] / max(IDLE95, .01), a(_j1.get("worker_policy")),
             _j1.get("workers_applied")))
    # ---- L6-C3c retired in this phase, on the same law that retired C3a's wall ratio ----
    # C3c used to assert canary_p95(workers=8) <= 1.6 x canary_p95(workers=1). Those two legs
    # now execute the SAME code path (both granted 1 by _batch_grant, both behind the same
    # service-wide gate), so any measured difference is machine noise, not product behaviour:
    # six runs on disk read 0.69x and 1.12x among other values, and the run-to-run spread on
    # unchanged code is wider than the defect it was meant to catch. The ratio is still
    # printed, unasserted, on the [REPORTED] line at the end of the suite. What C3c also
    # pinned -- "the granted budget never exceeds the published service cap" -- is asserted
    # more tightly now: L6-C3a requires workers_applied == 1 at 1/4/8, and L6-C4 requires
    # 1 <= applied <= CAP for two simultaneous 8-worker batches plus peak_concurrency == 1.
    # The pre-Phase-52 oversubscription this check was written against (canary 166.9 -> 2385.6
    # ms = 14.3x, wall 14.46/13.66/13.46 s for 1/4/8 workers, workers_applied absent) is
    # covered by C3a's counter, which sees the cross-request leak that clocks could not.

    # ---------- C4 concurrent batches share one service-wide budget ----------
    def two_batches():
        out = {}
        ths = []

        def _run(tag):
            out[tag] = post("/api/v1/batch/process",
                            {"file_paths": list(files), "operation": "remove_background",
                             "workers": 8}).json()
        for tag in ("a", "b"):
            t = threading.Thread(target=_run, args=(tag,))
            t.start()
            ths.append(t)
        for t in ths:
            t.join()
        return out

    _w, _p50, _p95, _n, _lag, res, _sp2 = measure(two_batches)
    _mb = batch_metrics()
    _applied = [res["a"].get("workers_applied"), res["b"].get("workers_applied")]
    check("L6-C4 the worker budget is a property of the SERVICE, not of one request: /metrics "
          "publishes worker_cap and a measured peak_concurrency, and after two simultaneous "
          "8-worker batches (16 workers requested in total) the peak concurrency actually "
          "reached must still sit inside the single published cap, with each response reporting "
          "an applied budget no larger than it and in_flight back on the shelf. Before Phase 52 "
          "each request built its own semaphore, so 2 x 8 = 16 pixel kernels contended with the "
          "console and nothing anywhere reported the real number. This leg's own ceiling was "
          "widened out of a half-truth: the GREEN runs that followed F3 read cap=16 with "
          "peak=2 for 2 x 8 requested and this check called that inside budget, because peak "
          "was only compared with worker_cap -- but a batch that claims "
          "worker_policy=cpu_gil_bound_serialized while two of its kernels run at once is "
          "claiming something the counter contradicts, and that is L6-C3a's assertion now. C4 "
          "keeps the cap/applied/in_flight half and runs after C3a, so a peak above 1 here "
          "means the cross-request gate leaked.",
          isinstance(_mb.get("worker_cap"), int) and _mb["worker_cap"] >= 1
          and _mb.get("worker_cap") == CAP
          and _mb.get("peak_concurrency") == 1
          and all(isinstance(v, int) and 1 <= v <= CAP for v in _applied)
          and isinstance(_mb.get("in_flight"), int) and _mb["in_flight"] == 0,
          "cap=%s peak=%s in_flight=%s | applied a=%s b=%s (requested 8+8) | canary p95 during "
          "both=%.1f" % (_mb.get("worker_cap"), _mb.get("peak_concurrency"),
                         _mb.get("in_flight"), _applied[0], _applied[1], _p95))

    # ---------- B6 return to baseline ----------
    # Phase 69 form change: the adjudicating quantity moves from this client's wall clock to the
    # service's own reported time. Evidence for moving it, four consecutive runs of this layer on
    # ONE identical binary (p69_8_L6a/L6b, p69_8c_L6a/L6b): the wall median-to-median leg read
    # 2.49x (RED) / 1.07 / 1.73 / 1.08 while every counter in the same runs said the service was
    # identical -- the RED run had the FASTEST server-side /health median of the four (1.10 ms vs
    # 1.30 / 1.20 / 1.30), RSS growth 1.07x vs 1.06x, worker counters cap=16 peak=1 in_flight=0 in
    # all four, and a CALMER own-tail than the green runs (2.7x vs 4.8x / 2.9x / 4.7x). A ceiling
    # of 2.00 sitting inside the spread of unchanged code adjudicates the machine, not the
    # service; the wall clock stays measured and printed below, with nothing riding on it.
    # Thresholds 2.00x / TAIL_MAX are carried over verbatim -- the ratio was not loosened, only
    # the clock it is computed on. See the [BASELINE] sampler for the start-side collection.
    idle2 = []
    idle2_srv = []
    for _ in range(IDLE_SAMPLES):
        _s = time.perf_counter()
        _hb2 = get("/api/v1/health", to=10)
        idle2.append((time.perf_counter() - _s) * 1000.0)
        try:
            idle2_srv.append(float(_hb2.headers.get("X-Process-Time")) * 1000.0)
        except (TypeError, ValueError):
            pass
    _b6_s50, _b6_s95 = pct(idle_srv, .5), pct(idle_srv, .95)
    _b6_e50, _b6_e95 = pct(idle2_srv, .5), pct(idle2_srv, .95)
    check("L6-B6 after the whole load set the service returns to its own idle baseline, judged on "
          "the time the SERVICE reports for itself (X-Process-Time, which starts in "
          "metrics_middleware before any await) instead of on this client's wall clock: the end "
          "median must sit within 2x the start median AND the end sample's own tail (p95/p50) "
          "within %.0fx, AND both baselines must have collected all %d server-side samples -- a "
          "short or empty population is RED here, never silently exempt, because a permanent "
          "median degradation is exactly what this leg exists to catch (leaked threads or handles) "
          "and a missing counter would let it pass by not looking. Three things were re-derived "
          "here from the runs on disk. First, the old rule compared the two p95s, and at n=20 a "
          "p95 IS the maximum, which is how the post-fix run2 was read as 6.5 ms -> 29.6 ms (4.6x) "
          "on one unlucky poll; the idle p95 itself ranges 4.9-21.8 ms between runs, so any ratio "
          "to it is noise -- hence 60 samples and the own-tail form. Second, the P50_FLOOR=5.0 ms "
          "exemption this leg used to carry is DROPPED, not translated. It existed because a "
          "ratio of two medians pinned at the transport floor measures the floor (stake m3 turned "
          "this leg red as pure collateral at 2.23x with both medians at the round-trip floor), "
          "and the same reasoning against a sub-millisecond server clock would exempt everything, "
          "so keeping it here would have been a relaxation dressed as a port. Third, the spread "
          "of the quantity now demoted to observation, on identical code: 1.00 / 1.21 / 1.23 / "
          "1.24 / 1.33 / 1.43 / 2.23 historically and 2.49 / 1.07 / 1.73 / 1.08 in the four runs "
          "above, i.e. the 2.00 ceiling sat inside the noise band; this is the same law the layer "
          "already learned on its own D4 leg (one 7,184.3 ms server-side dt on an idle /health, "
          "and max-of-3 wall tails of 20-58 ms printed in EVERY run, green ones included). Both "
          "baselines take %d samples and the ratios 2.00 / %.0f are the previous form's own "
          "numbers" % (TAIL_MAX, IDLE_SAMPLES, IDLE_SAMPLES, TAIL_MAX),
          len(idle_srv) == IDLE_SAMPLES and len(idle2_srv) == IDLE_SAMPLES
          and _b6_e50 <= 2.0 * max(_b6_s50, 0.05)
          and _b6_e95 <= TAIL_MAX * max(_b6_e50, 0.05),
          "ADJUDICATED server-side: start p50=%.3f p95=%.3f -> end p50=%.3f p95=%.3f | drift "
          "%.2fx (allow 2.00) end tail %.1fx (allow %.0f) | n=%d+%d of %d | OBSERVATION ONLY "
          "client wall: start p50=%.1f -> end p50=%.1f (%.2fx), own tail %.1fx, p95 %.1f -> %.1f "
          "(%.2fx)"
          % (_b6_s50, _b6_s95, _b6_e50, _b6_e95,
             _b6_e50 / max(_b6_s50, .05), _b6_e95 / max(_b6_e50, .05), TAIL_MAX,
             len(idle_srv), len(idle2_srv), IDLE_SAMPLES,
             IDLE50, pct(idle2, .5), pct(idle2, .5) / max(IDLE50, .01),
             pct(idle2, .95) / max(pct(idle2, .5), .01), IDLE95, pct(idle2, .95),
             pct(idle2, .95) / max(IDLE95, .01)))

    # ---------- D3 memory ----------
    if START_RSS is not None:
        try:
            _now = _tree_rss()
            check("L6-D3 memory stays bounded across the whole load set: process-tree RSS after "
                  "snapshots + delivery + 195 imports + 5 batches within 2.5x the RSS at startup. "
                  "The pre-Phase-52 spike recorded 111.9 MB -> 127.6 MB (+14%) and the two GREEN "
                  "runs of this layer measured 1.09x growth, so the ceiling is a regression "
                  "budget, not a restatement of the observation",
                  _now <= 2.5 * START_RSS,
                  "start=%.1fMB end=%.1fMB growth=%.2fx" % (START_RSS, _now, _now / START_RSS))
        except Exception as e:
            skip("L6-D3 RSS growth across the load set", "psutil unavailable at end: %s" % a(e))

    # ---------- D4 console poll cost ----------
    # D4 v1 (Phase 52-57) judged max of 3 client-side wall samples against absolute
    # millisecond bounds. Phase 58 replaced that form after measuring, on this machine, that
    # the form cannot adjudicate: outputs/p58_l6d4_parts.json recorded a 7,184.3 ms
    # SERVER-side dt (X-Process-Time, which starts in metrics_middleware before any await) on
    # an idle /health whose neighbours in the same run read 3.8 / 9.7 / 1.2 / 1.3 / 1.3 ms.
    # A handler with no await point can only be slow because the process was off-CPU, so that
    # sample is the machine taking the thread away -- no product change removes it, and
    # "max of 3" is one such hiccup wide by construction. The same measurement round, run on
    # identical code with only the service restarted, read idle p50 16.2 ms vs 8.1 ms, a
    # loop-lag floor of 26.3 vs 16.8 ms, B1 1.55 s vs 0.63 s and B4 497.9 vs 326.9 ms per
    # query, while C: free fell 0.788 -> 0.612 GB under external writers. That is the L6-B4 /
    # C3c law re-learned on this layer's own last red: absolute clocks and max-of-N tails
    # adjudicate nothing here.
    # What v1 got RIGHT was the question. /api/v1/metrics really is 10-40x every other
    # endpoint the console polls, and the two names responsible were invisible to L6-A1
    # because neither was in its tables. outputs/p58_l6d4_parts.json attributes the whole
    # handler: psutil.Process(...).num_threads() = 11.82 ms p50 / 14.45 ms max on a quiet
    # machine, 51.48 / 189.43 ms while the suite's batch load runs, 85.91 / 144.28 ms in the
    # closing round -- against handler_body_total 24.03 / 47.19 ms and every other segment
    # <= 0.1 ms (latencies_n=23, route_hits_n=2, resp_bytes=683 all read 0.00). It costs that
    # much because Toolhelp32 snapshots EVERY thread in the machine, so the price is set by
    # other processes, not by this product: the second call inside the same request was
    # 11.99/28.36 ms, and the post-load samples track the machine's thread count sample for
    # sample. shutil.disk_usage(DATA_ROOT) is the second name: 0.15 ms p50 / 3.42 ms max
    # quietly, but outputs/p58_l6d4_report.json measured it at 2.0x the /metrics median and
    # 4.7x its maximum while a sustained 4 MB/fsync writer ran on the data volume, with
    # /health, /capabilities and /openapi.json standing still at 1.2-1.3 / 3.8-3.9 / 2.3-2.5
    # ms in the same window -- GetDiskFreeSpaceExW waits behind volume write contention, so
    # a console poll was inheriting the latency of whoever else was writing to that drive.
    # Ablation with both names behind a cache, under EVEN HEAVIER contention (chunk cost
    # 118.9-238.9 ms vs 71.5-138.6 ms): /metrics server-time median fell 42.9 -> 35.7 ms with
    # the max falling 364.7 -> 87.1 ms, while /health stayed at 1.3 ms.
    # A third finding arrived with the fix itself: /metrics' cpu_percent had been a constant
    # 0.0 since the field existed (p58_cpu_semantics.json), so leg 4b is the counter-leg that
    # keeps a published-but-immovable process counter from passing as a metric.
    # So D4 v2 keeps the question and splits it into falsifiable legs, each in a different
    # currency: (1) an absolute gross bound, only where absolute ms is honest --
    # the one-shot lazy-schema cost; (2) a same-round median RATIO against /health, the
    # service's own X-Process-Time rather than this client's wall clock; (3) the tail as a
    # COUNT, because the 7,184 ms sample proves the tail is not a product property; (4) a
    # COUNTER leg on the product's own snapshot worker, the only leg that can tell "the
    # numbers are cached" apart from "the numbers are stale or never built", and a 4b half
    # that keeps a published-but-immovable process counter from passing as a metric.
    # Legs 2, 4 and 4b all read RED against the pre-Phase-58 product: ratio 42.9/1.2 = 35x
    # against a 3x ceiling, no metrics_snapshot block at all, and cpu_percent pinned at 0.0.
    _d4_paths = ("/api/v1/health", "/api/v1/capabilities", "/api/v1/metrics")
    _wall = {p: [] for p in _d4_paths}
    _srv = {p: [] for p in _d4_paths}
    for _i in range(D4_SAMPLES):                     # round-robin: one window, not three
        for p in _d4_paths:
            _s = time.perf_counter()
            _r = get(p, to=300)
            _wall[p].append((time.perf_counter() - _s) * 1000.0)
            try:
                _srv[p].append(float(_r.headers.get("X-Process-Time")) * 1000.0)
            except (TypeError, ValueError):
                pass                                 # leg 2 skips, leg 3 still has wall samples
    _openapi = []
    for _ in range(3):
        _s = time.perf_counter()
        get("/openapi.json")
        _openapi.append((time.perf_counter() - _s) * 1000.0)
    _hb = max(pct(_srv["/api/v1/health"], .5), 0.01)
    _ratios = {p: pct(_srv[p], .5) / _hb for p in ("/api/v1/capabilities", "/api/v1/metrics")
               if _srv[p]}
    _tails = {p: sum(1 for v in _wall[p] if v > D4_TAIL_MS) for p in _d4_paths}
    _snap1 = (get("/api/v1/metrics").json() or {}).get("metrics_snapshot")
    time.sleep(D4_WAIT_S)                            # no /metrics call inside the wait
    _snap2 = (get("/api/v1/metrics").json() or {}).get("metrics_snapshot")
    _snap_ok = False
    _snap_note = "absent"
    if isinstance(_snap1, dict) and isinstance(_snap2, dict):
        _iv = _snap2.get("interval_s")
        _snap_ok = (_snap2.get("status") == "ok"
                    and isinstance(_snap2.get("refreshes"), int)
                    and isinstance(_snap1.get("refreshes"), int)
                    and _snap2["refreshes"] > _snap1["refreshes"]
                    and isinstance(_iv, (int, float)) and _iv >= 1.0
                    and isinstance(_snap2.get("age_ms"), (int, float))
                    and _snap2["age_ms"] <= 4 * _iv * 1000.0)
        _snap_note = ("status=%s refreshes %s->%s over %.1fs interval=%ss age_ms=%s "
                      "build_ms=%s errors=%s" % (
                          a(_snap2.get("status")), _snap1.get("refreshes"),
                          _snap2.get("refreshes"), D4_WAIT_S, a(_iv),
                          a(_snap2.get("age_ms")), a(_snap2.get("last_build_ms")),
                          a(_snap2.get("errors"))))
    # ---- leg 4b: the cached counters must be LIVE, not merely published ------------------
    # Phase 58 found that /metrics' cpu_percent had been a permanent 0.0 for the whole life of
    # this field. Not a typo -- psutil.Process.cpu_percent(interval=None) is a DELTA against the
    # previous call on the SAME instance, and the handler built a fresh psutil.Process inside
    # every request, so there was never a previous call to subtract. Measured pair
    # (p58_cpu_semantics.json, psutil 7.2.2, same process, burn loop between samples):
    #   fresh instance per call    = [0.0, 0.0, 0.0, 0.0]
    #   persistent handle per call = [0.0, 31.2, 34.7, 34.7]
    # A counter that cannot move is the reason L6-B0 exists for loop-lag and L6-C4 for
    # peak_concurrency; it now applies to the snapshot's own process counters. The load that
    # should push it off zero is real work through the product (3200x2000 images through
    # /api/v1/batch/process at workers=1), and the same poll must SEE the work in flight
    # (batch.in_flight >= 1), so the reading cannot be credited to some other process on the
    # machine. Sample count >= 2 is the non-vacuity control: an enumerator that reads nothing
    # is RED, not green.  BURST SIZE AND POLL SPACING ARE MEASURED, NOT GUESSED: the first
    # form of this leg sent ONE image (p58_d4v2_4b_red_L6.log: batch total_time=0.361 s) and
    # at 0.4 s spacing collected exactly 1 sample, which saw in_flight=0 -- a RED for two
    # causes at once, only one of which was the product. So the burst sends 8 images
    # (~2.9 s of the suite's own measured per-file cost) and polls every 0.2 s, which puts
    # ~14 polls inside the window. Widening the ruler is allowed; lowering the bar is not:
    # the count floor stayed at 2 and the same run still reports the sample list, so if the
    # window is ever too short again the leg fails loudly instead of passing quietly.
    _cpu_samples, _inflight_samples, _poll_err = [], [], None
    _disk_seen = 0
    _burst = {"resp": None}

    def _cpu_burst_load():
        try:
            _burst["resp"] = post("/api/v1/batch/process",
                                  {"file_paths": [str(big)] * 8,
                                   "operation": "remove_background",
                                   "workers": 1}).json()
        except Exception as e:
            _burst["resp"] = {"_error": str(e)[:200]}

    _bt = threading.Thread(target=_cpu_burst_load)
    _bt.start()
    while _bt.is_alive():
        try:
            _mj = get("/api/v1/metrics", to=60).json() or {}
            if "disk_free_gb" in _mj:
                _disk_seen += 1          # the snapshot DID build (shutil branch ran)
            if "cpu_percent" in _mj:
                _cpu_samples.append(float(_mj.get("cpu_percent")))
                _inflight_samples.append((_mj.get("batch") or {}).get("in_flight"))
        except Exception as e:
            _poll_err = str(e)[:120]
        time.sleep(0.2)
    _bt.join()
    _cpu_live = [i for i, v in enumerate(_cpu_samples)
                 if v > 0.0 and (_inflight_samples[i] or 0) >= 1]
    # psutil genuinely absent is a DIFFERENT fact from a broken instrument: the builder only
    # publishes cpu_percent inside `if psutil:`, so "no cpu_percent but a real disk_free_gb"
    # is that signature, and it is exempt -- with the exemption printed, never silent.
    _cpu_exempt = (not _cpu_samples and _disk_seen > 0
                   and isinstance(_snap2, dict) and _snap2.get("status") == "ok")
    _cpu_ok = _cpu_exempt or (len(_cpu_samples) >= 2 and bool(_cpu_live))
    _cpu_note = ("samples=%d cpu=%s in_flight=%s live_at=%s exempt=%s batch=%s%s"
                 % (len(_cpu_samples),
                    a(json.dumps(_cpu_samples[:8])),
                    a(json.dumps(_inflight_samples[:8])), a(_cpu_live[:4]),
                    _cpu_exempt,
                    a(json.dumps({k: _burst["resp"].get(k) for k in
                                  ("success", "failed", "workers_applied", "total_time")})
                  if isinstance(_burst["resp"], dict) else "none"),
                    (" | poll_err=%s" % a(_poll_err)) if _poll_err else ""))
    check("L6-D4 the endpoints the web console polls must stay cheap, judged in counters and "
          "same-round ratios rather than absolute milliseconds (Phase 58 rewrite; its five legs "
          "and the measurements that forced the form are documented above this check). "
          "LEG1 warm-up gross bound: max of 3 /openapi.json calls <= %.0f ms -- kept absolute "
          "on purpose, because what it pins is a one-shot cost that used to land inside the "
          "first console poll (414 ms pre-Phase-52, FastAPI building the schema lazily); the "
          "product warms it at startup behind asyncio.to_thread, so the work moved and this "
          "layer did not move the goalposts. LEG2 steady-state median ratio: p50 of the "
          "service's own X-Process-Time for /api/v1/capabilities and /api/v1/metrics within "
          "%.1fx that of /api/v1/health in the same round-robin window, with a %.0f ms floor "
          "so a health median sitting at the observation floor cannot make the ratio "
          "meaningless (the B6 exemption, re-applied here). LEG3 the tail as a count: at most "
          "%d of %d samples on any polled path may exceed %.0f ms -- the alternative is a "
          "check that goes red because Windows took this process off-CPU for 7.2 seconds. "
          "LEG4 the cache must be alive: /metrics publishes metrics_snapshot with status=ok, a "
          "refresh counter that strictly increases across a %.0f s wait in which /metrics is "
          "never called, an interval of at least 1 s, and an age no worse than 4 intervals. "
          "This is the leg that separates 'answered from a snapshot' from 'answered from a "
          "stale or broken snapshot', and it is the reason the fix is not a silence trick. "
          "LEG4b the cached process counters must be LIVE: while eight real 3200x2000 images run "
          "through /api/v1/batch/process at workers=1 (a ~2.9 s window at this suite's own "
          "measured per-file cost), at least one of >=2 /metrics polls polled every 0.2 s that "
          "also see batch.in_flight >= 1 must report cpu_percent > 0.0 -- for the whole life of "
          "this field it reported a constant 0.0, because psutil's cpu_percent is a delta "
          "against the previous call on the same instance and the builder made a fresh "
          "Process per snapshot (measured pair in p58_cpu_semantics.json: fresh [0.0,0.0,0.0,0.0] "
          "vs persistent [0.0,31.2,34.7,34.7]). A build with no psutil at all is exempt, and "
          "the exemption is printed, never silent."
          % (D4_GROSS_MS, D4_RATIO, D4_ABS_MS, D4_TAIL_MAX, D4_SAMPLES, D4_TAIL_MS, D4_WAIT_S),
          max(_openapi) <= D4_GROSS_MS
          and len(_ratios) == 2 and all(
              v <= max(D4_RATIO * _hb, D4_ABS_MS) for v in _ratios.values())
          and all(v <= D4_TAIL_MAX for v in _tails.values())
          and _snap_ok and _cpu_ok,
          "L1 openapi max=%.1f (allow %.0f) | L2 health p50=%sms -> caps ratio=%sx "
          "metrics ratio=%sx (allow %.1fx or floor %.1fms = %.2fms) | L3 tails %s (allow %d "
          "of %d over %.0fms) | L4 %s | L4b %s || raw p50 caps=%s metrics=%s health=%s | wall "
          "p50 caps=%.1f metrics=%.1f | v1 form would print max wall %s"
          % (max(_openapi), D4_GROSS_MS, _fnum(pct(_srv["/api/v1/health"], .5)),
             _fnum(_ratios.get("/api/v1/capabilities")),
             _fnum(_ratios.get("/api/v1/metrics")),
             D4_RATIO, D4_ABS_MS, max(D4_RATIO * _hb, D4_ABS_MS),
             a(json.dumps(_tails, default=str)), D4_TAIL_MAX, D4_SAMPLES, D4_TAIL_MS,
             _snap_note, _cpu_note, _fnum(pct(_srv["/api/v1/capabilities"], .5)),
             _fnum(pct(_srv["/api/v1/metrics"], .5)),
             _fnum(pct(_srv["/api/v1/health"], .5)),
             pct(_wall["/api/v1/capabilities"], .5), pct(_wall["/api/v1/metrics"], .5),
             a(json.dumps({p: round(max(_wall[p]), 1) for p in _d4_paths}))))

    # ---------- reported (not asserted) ceilings, so the report has real numbers ----------
    print("[REPORTED] idle p50=%.1f p95=%.1f | batch canary p95 w1=%.1f w4=%.1f w8=%.1f | "
          "batch wall w1=%.2f w4=%.2f w8=%.2f | search N=%d t=%.4fs | start RSS=%s"
          % (IDLE50, IDLE95, INFO[1][2], INFO[4][2], INFO[8][2],
             WALL[1], WALL[4], WALL[8], n_big, t_big,
             a("%.1f" % START_RSS) if START_RSS else "unavailable"))
    _body_done = True
finally:
    # Phase 58 teardown rework. The run logged in p58_b2_joint_green_L6.log decided EVERY check --
    # 17 [PASS] lines, zero FAIL, zero SKIP -- and then died in teardown on
    #   subprocess.TimeoutExpired: taskkill /PID 37272 /T /F timed out after 30 seconds
    # so no TOTAL line was ever printed and the log adjudicated nothing. That is this loop's worst
    # instrument failure: an environment hiccup on the kill path (the machine had just been handed
    # a 120 MB delivery package plus eight 3200x2000 batch images and its temp root lives on a
    # volume with GB-scale concurrent writers) converts a COMPLETED run into a non-evidence, and
    # the abort also skipped srv_log.close()/rmtree, leaking zjl6_w8rk268m. Two changes, neither of
    # which touches an assertion: (1) the adjudication is printed FIRST inside the finally, so no
    # teardown step can ever stand between a finished load set and its verdict; (2) every teardown
    # step is individually guarded and the kill uses Popen + DEVNULL instead of capture_output --
    # subprocess.run(timeout=) reads its captured pipes again after the timeout with NO deadline on
    # Windows, so a hung child could keep that read alive past the 30 s the caller thought it had.
    # A load set that RAISED must never print a green verdict, which is what _body_done is for:
    # it separates "teardown hiccup after 17 decided checks" from "the checks themselves aborted".
    GO["v"] = False
    fails = [x for x in results if x[0] == "[FAIL]"]
    print("=" * 64)
    if _body_done:
        print("L6 TOTAL %d | PASS %d | FAIL %d | SKIP %d"
              % (len(results), len(results) - len(fails), len(fails), len(skipped)))
        for _t, _n, _d in results:
            if _t == "[FAIL]":
                print("  FAIL: %s" % a(_n)[:200])
        for _n, _r in skipped:
            print("  SKIP: %s (%s)" % (a(_n)[:140], a(_r)[:140]))
        print("[CITATION] suite source: %s" % a(Path(__file__).resolve()))
    else:
        print("[ABORT] the load set raised before finishing -- NO VERDICT LINE IS EMITTED "
              "(a log with no TOTAL line adjudicates nothing; see the traceback above)")
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception as _e:
        print("[TEARDOWN] terminate step did not finish cleanly: %s" % a(repr(_e))[:160])
    _dk_rc = "not-run"
    try:
        _dk = subprocess.Popen(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            _dk_rc = _dk.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _dk.kill()
            try:
                _dk_rc = "killed-after-timeout(%s)" % _dk.wait(timeout=5)
            except Exception as _e2:
                _dk_rc = "killed-after-timeout(%s)" % a(repr(_e2))[:80]
    except Exception as _e:
        _dk_rc = "taskkill step raised %s" % a(repr(_e))[:160]
    try:
        srv_log.close()
    except Exception as _e:
        print("[TEARDOWN] service log close: %s" % a(repr(_e))[:120])
    time.sleep(1.5)
    _still = "unknown"
    try:
        _still = _answered(PORT)
    except Exception as _e:
        print("[TEARDOWN] port recheck: %s" % a(repr(_e))[:120])
    for _attempt in range(3):
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception as _e:
            print("[TEARDOWN] rmtree attempt %d: %s" % (_attempt, a(repr(_e))[:120]))
        if not tmp.exists():
            break
        time.sleep(2.0)
    print("[TEARDOWN] taskkill=%s port still answering=%s temp root exists=%s"
          % (_dk_rc, _still, tmp.exists()))
sys.exit(1 if fails else 0)
