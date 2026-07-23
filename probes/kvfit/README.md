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
