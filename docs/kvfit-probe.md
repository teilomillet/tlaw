# kvfit interface probe

Date: 2026-07-22

Status: second real-codebase probe, after retrain. kvfit was read, not
modified. tlaw v0.0.2 (domains, `same`, canary) was the version under test.

## Target

kvfit is a published GPU memory capacity planner. Its `plan_topologies`
function is pure arithmetic with self-describing output: every layout it
returns carries a verdict ("fits", "weights-oom", "context-oom",
"unsupported") next to the numbers that supposedly justify it. The probe
question: can tlaw state that justification as executable laws without
modifying or wrapping kvfit?

## How the probe ran

No change to kvfit, no change to its environment. The laws file lived in a
scratch directory and ran with:

```console
uv run --project ~/Code/kvfit --with ~/Code/tlaw tlaw check laws.py
```

`--project` supplies kvfit's own environment; `--with` injects tlaw for the
run only. This is the reusable pattern for probing any repository without
touching its pyproject.

## Laws that passed

Four laws, all passing on first contact with the real planner
(4 passed in 0.16s):

1. **every layout partitions the gpus and conserves sequences** —
   `tp * dp == gpus` and `total == per_replica * dp` for every layout;
2. **every verdict is justified by its own reported numbers** — "fits"
   implies `weights + sequences * kv <= budget`; "weights-oom" implies
   `weights > budget`; "context-oom" implies weights fit but one cache does
   not; "unsupported" implies the head-divisibility failure it claims;
3. **raising utilization never lowers any layout's capacity**; and
4. **heavier weights never raise capacity**.

Law 2 is the interesting shape: it does not re-derive the planner's answer,
it checks that the answer is *internally consistent* — the verdict must be
entailed by the numbers printed beside it. That pattern should transfer to
any tool whose output explains itself.

## Verified bottlenecks

### 1. No integer domain

Every generated input in this probe was a bounded integer (gpu counts,
head counts, weight bytes) or a bounded float. `tlaw.number` is floats
only, so every law needed the explicit Hypothesis form. Unbounded `int`
annotations were not an option: `plan_topologies` scans divisors up to
`gpus`, so a huge generated gpu count would hang the law.

Resolved the same day: `tlaw.integer(low, high)` is implemented, with this
probe as the demonstrated example the interface-budget rule requires. The
follow-up run below rewrote the probe laws in the bare form using it.

### 2. The explicit form silently loses the deterministic posture

Because the first run used `@tlaw.law("description", value=strategy)`, none
of these laws ran with the bare form's guarantees: no derandomized
examples, no input-mutation guard. For probe laws that is survivable; for
committed evidence it was a silent inconsistency between the two forms.

Resolved by reporting, not by force: tlaw cannot impose deterministic
defaults on the explicit form because authors stack their own `@settings`
on top and Hypothesis rejects a second application. Instead `--explain`
now states the posture per law ("explicit strategies; Hypothesis settings
decide examples and randomization; no input-mutation guard"), so the
difference is a visible choice rather than a discovery.

### 3. Constructor boilerplate is the remaining cost

`CacheEstimate` and `Hardware` require many fields irrelevant to the laws;
two small local builders (`estimate(...)`, `probe_hardware(...)`) were
needed. This is acceptable — the builders are visible flow steps beside the
laws — but a future related-inputs design should be probed against exactly
this shape.

## Follow-up run, same day

After `tlaw.integer` landed, the four laws were rewritten in the bare form
with module-level domains (`Gpus = tlaw.integer(1, 8)` and so on) and rerun
with the same non-invasive command. All four passed again (0.17s), this
time with the bare form's full posture — deterministic examples, no example
database, input-mutation guard — and `--explain` reporting the complete
generated contract per law:

```text
every verdict is justified by its own reported numbers: gpus: integer(1, 8),
heads: integer(1, 64), kv_gib: number(0.001, 64), ...; up to 100 examples;
deterministic, no example database
```

The probe therefore ended with zero remaining uses of raw Hypothesis
syntax; both first-run bottlenecks were closed by the findings they
produced.

## Not exercised in this first probe

`tlaw mutate` was not run during this first probe: kvfit is package-shaped
(`src/kvfit/*.py`), and the then-current flat-directory boundary did not
apply. The canary would correctly refuse. That finding paid for the package
boundary implemented and measured in
[`package-boundary-probe.md`](package-boundary-probe.md).
