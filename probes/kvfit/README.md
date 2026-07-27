# kvfit probe: retained evidence

This directory retains the artifacts behind the "39 of 40" claim in
[docs/package-boundary-probe.md](../../docs/package-boundary-probe.md) so the
claim is **independently rerunnable**, not just something that once happened
on one machine. Retention is the condition tlaw sets on its own docs: a
result may be published as evidence only when its inputs are committed and
anyone can reproduce it.

## What is here

- `laws.py` — the 15 laws written against kvfit's `plan_topologies`,
  `summarize_concurrency`, and `TopologyEstimate.as_dict`.
- `equivalents.toml` — the equivalence ledger. It holds one entry, for the
  mutant proven equivalent by reading the code (not by a search that failed
  to find a counterexample).
- `report.json` — the recorded `tlaw.mutation/v1` report. Its `laws` and
  `implementation` fields are absolute paths from the machine that recorded
  it; everything else — mutation IDs, outcomes, diffs, and the summary — is
  portable and is the evidence. The command below regenerates it anywhere.
  It predates the `kill_kind` and `killed_by_timeout` fields added in tlaw
  0.0.5, so it is a valid but older `tlaw.mutation/v1` document; a fresh run
  carries those two fields, and the durations it records are what
  [the section below](#what-the-retained-39-of-40-rests-on) reads.

## The pinned target

The probe runs against **kvfit at commit
`f48a6abdef9795028332ae1cf2b74f091929e01c`**
(`feat: detect integrated DSpark cache`, `github.com/teilomillet/kvfit`).
kvfit is read, never modified. Pinning matters because the laws import
kvfit internals; a later kvfit revision may generate different mutants.

## Reproduce

With kvfit checked out at
`f48a6abdef9795028332ae1cf2b74f091929e01c` and its environment available,
from the tlaw repository root:

```console
uv run --project /path/to/kvfit --with . tlaw mutate \
    probes/kvfit/laws.py \
    /path/to/kvfit/src/kvfit/planner.py \
    --equivalents probes/kvfit/equivalents.toml
```

Expected:

```text
Boundary: package (kvfit.planner)
Canary: KILLED (laws observe the mutated module)
Killed 39 of 40 runnable generated mutations.
Survivors: 0
Unresolved equivalent candidates: 1
```

Exit code `0`: every mutant is killed or a committed, explained equivalent.

A `Kills by calibrated timeout` line may also appear, and how many it counts
depends on how fast the machine is. That is not noise in the run; it is the
finding recorded below.

## What the retained "39 of 40" rests on

Re-running this probe on 2026-07-27 reproduced it exactly: the same 40
mutation IDs, the same outcome for every one, the same ledgered equivalent,
exit code `0`. The headline number is stable.

What is *not* stable across machines is the kind of evidence behind it. Five
of the recorded 39 kills are **calibrated-timeout kills**, and the report as
first recorded did not say so:

| Mutant | Change |
| --- | --- |
| `22596609f7ca` | `_candidate_tp_sizes`: `gpus < 1` → `not gpus < 1` |
| `c588f31eebe0` | `_candidate_tp_sizes`: `requested_tp is not None` → negated |
| `19b29fea9ff5` | `_candidate_tp_sizes`: `scale_up_domain_accelerators is None` → negated |
| `0ed3a8a8c370` | `plan_topologies`: `weight_bytes < 0` → negated |
| `c7279398ba8d` | `plan_topologies`: `not 0 < utilization <= 1` → negated |

On the machine that recorded `report.json` the baseline ran in `0.78s`, so
the per-mutant budget was `7.83s` (10x), and all five hit it at `7.8s` with
exit code `124`. On the machine that re-ran it the baseline took `2.69s`,
the budget was `26.87s`, and the same five finished in `13.6–16.8s` and were
rejected by 11 to 15 laws each.

So they are not infinite loops. They terminate; negating those conditions
widens the candidate tensor-parallel set, and the laws then do five to six
times the re-run baseline's work. A budget derived from the baseline by a fixed
multiplier is systematically tight for exactly the mutants that make the
implementation do more, and whether such a mutant is recorded as *laws
rejected it* or as *it ran out of budget* depends on how fast the machine is.

Both are honest kills — a run that exceeds a calibrated budget really did
behave differently from the baseline. But a timeout kill names no law, so it
cannot appear in the which-law-defends-this reading that
[`docs/law-first-experiment.md`](../../docs/law-first-experiment.md) calls
the strongest thing a mutation report produces. On the recording machine,
five of these 39 kills contributed nothing to that reading, and nothing in
the report said so.

Since tlaw 0.0.5 the report says it. Each mutation carries a `kill_kind`,
and a run like the original one now prints:

```text
Killed 39 of 40 runnable generated mutations.
Kills by calibrated timeout (no law attribution): 5
```

The re-run omits that line entirely, because on slower hardware none of the
five timed out and the line is printed only when it counts something. That is
the point: the difference becomes visible where it exists, instead of having
to be inferred from durations.

Two smaller findings from the same re-run, both fixed rather than recorded
as quirks:

- `0ed3a8a8c370` was recorded as a timeout kill that *also* named 14 laws.
  Those names were scraped from whatever pytest had printed before the
  budget stopped it — a truncated prefix, not the set of laws that rejected
  the mutant. A timed-out run is no longer scraped for attribution.
- All five were recorded as `replay confirmed` under a guard that only
  compared the word "killed". Two timeouts agreeing on a label is not
  reproduction; a replay must now return the same kill kind, and a timeout
  kill is re-timed against a baseline re-measured next to it.

## The one equivalent

Mutant `617903f6c6bb` changes `size <= scale_up` to `size < scale_up` in the
candidate filter. It cannot change the result: a second clause
`size % scale_up == 0` always keeps `size == scale_up`, because a number is
divisible by itself. This is proven by reading the filter, so it is a
ledgered equivalent rather than a survivor — visible in the report and the
runnable denominator forever.

## Why this lives in `probes/`, not `examples/`

The examples are self-contained and run in the push gate on every commit.
This probe depends on an external, separately-installed project, so it
cannot run in the gate. It is retained as evidence and reproduced on demand,
not enforced continuously.
