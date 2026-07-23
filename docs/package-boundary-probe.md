# Package mutation boundary probe

Date: 2026-07-22

Status: **resolved** — the mechanism below is implemented as the package
boundary in `tlaw/mutation.py`. kvfit was read, not modified. The design
was validated by hand simulation before any engine change, so it was
chosen from measurements rather than from argument; the shipped engine
then reproduced the simulated numbers exactly.

## Why kvfit is the right baseline

Both earlier probes hit this limitation, so a fix needs a target that is
honest about real Python packaging. kvfit is:

- **src-layout** (`src/kvfit/*.py`) — the most common shape;
- **editable-installed** into the environment the laws run in;
- **already covered by passing laws** from `docs/kvfit-probe.md`, so the
  baseline is a known-good starting point rather than a new spec; and
- **pure enough to mutate**: `planner.py` yields 40 single-site mutants
  (13 arithmetic, 13 negations, 12 comparison boundaries, 2 skipped calls).

Before this probe, `tlaw mutate laws.py src/kvfit/planner.py` refused:

```text
tlaw: laws and implementation must share one directory: that directory is
the boundary tlaw copies for every isolated run
```

## The mechanism, validated before implementing

The candidate fix is **copy the package, shadow the installed one**:

1. find the package root by walking up from the module while `__init__.py`
   exists (`src/kvfit/planner.py` → package `kvfit`, root parent `src/`);
2. copy the whole package into a fresh workspace;
3. write the mutant over its module inside the copy; and
4. run the laws with the workspace ahead of the installed package on the
   import path.

Copying the **whole package** rather than one file matters: intra-package
imports (`from kvfit.models import GIB`) then resolve inside the copy, so
one coherent world is mutated — the same guarantee the flat boundary gives.

Three measurements, all from the real run:

| Question | Result |
| --- | --- |
| Does the shadow actually load? | **Yes** — poisoned package: laws failed (canary died) |
| Does the unmutated copy still pass? | **Yes** — baseline exit 0 |
| Do real mutants die? | **16 of 40 killed**, 0 invalid, 0 errors, 50s total |

The canary is what makes this safe to ship: if a project's install shape
defeats path shadowing — for example a strict-editable install that uses a
`MetaPathFinder` rather than a `.pth` entry — the poisoned package still
loads the *installed* code, the laws pass, and tlaw reports INVALID
EVIDENCE instead of 40 false survivors. The guard added for flat
boundaries already covers the package case unchanged.

## What the 24 survivors say

The survivors are not tool failures; they are an inventory of missing laws.
The saved laws and current planner were rerun on 2026-07-23 to account for
every original survivor. The 24 split into five groups:

**1. Uncalled surfaces (6 survivors).** Three mutations were inside
`TopologyEstimate.as_dict()` and three inside `summarize_concurrency()`.
The four probe laws call neither surface. Honest gap, easy to name.

**2. Self-consistent calculations and untested boundaries (6 survivors).**
These changed the utilization lower boundary, weight or KV scaling, the exact
zero-KV boundary, or the one-sequence verdict boundary. The planner still
agreed with its own reported numbers, so the original equations could not
distinguish the changes.

**3. Optional inputs and layout completeness (9 survivors).** The laws did
not require every default TP candidate and never supplied an explicit
`tensor_parallel` or `scale_up_domain_accelerators`. This is the *same* gap
that made the kvfit probe skip a law earlier: a valid explicit
`tensor_parallel` must divide `gpus` — a dependency whose domain is a finite
computed set.

Closed by `tlaw.one_of`, which this survivor group paid for. With
`depends` supplying the wiring and `one_of` stating the choices, the law
that had gone unwritten is one declaration:

```python
TensorParallel = tlaw.depends(
    lambda gpus: tlaw.one_of(
        [size for size in range(1, gpus + 1) if gpus % size == 0]
    )
)
```

Adding it killed four more mutants, all attributable to that law alone: two
inside explicit-TP validation and two that removed a layout append.

**4. Missing result cardinality (2 survivors).** Removing either append that
returns the one requested layout still satisfied the original universal laws:
an empty result made every `all(...)` true. The explicit-TP law above requires
exactly one result and killed both.

**5. The interesting one (1 survivor).** `hardware.memory_gib * GIB`
mutated to `/ GIB` survived. The budget becomes absurd, yet every law still
passes: the monotonicity laws are scale-invariant, and the
verdict-justification law checks each layout against *its own reported*
budget, so a globally wrong budget stays internally consistent.

That is a real limitation of the self-justification pattern praised in
`docs/law-first-experiment.md`: a law that checks output against output
cannot catch an error in the shared input to both sides. Killing it needs
one law anchored to an external fact — for example, that a layout's budget
equals `memory_gib * utilization` in bytes. Mutation testing found the
blind spot in the law-writing *style*, not just in a specific law.

## Cost

40 mutants against 4 laws took 50 seconds without replay. With the engine's
replay-every-kill rule that roughly doubles for the killed set. Package
copying was not the bottleneck; process startup dominates. This is the
first real data point for the cost note in `docs/mutation-evidence.md`.

## Design consequences for the engine

- The boundary becomes **the package root**, not the containing directory,
  detected from `__init__.py` chains; the flat case stays exactly as it is
  and remains the default for feature folders.
- The laws file no longer has to live beside the implementation: for
  package targets it may live anywhere, since the package is placed on the
  import path rather than being imported as a sibling module.
- The mutated module must be addressed by its **dotted name** inside the
  copied package so mutation IDs stay stable and readable
  (`kvfit.planner` rather than `planner`).
- Nothing about outcomes, replay, the canary, or the equivalence ledger
  changes: they are boundary-independent by construction.

## Outcome

The engine now accepts a package target directly:

```console
$ tlaw mutate laws.py ~/Code/kvfit/src/kvfit/planner.py
Boundary: package (kvfit.planner)
Baseline: PASS (0.40s); per-mutant budget 5.0s
Canary: KILLED (laws observe the mutated module)
Killed 16 of 40 runnable generated mutations.
```

Identical to the hand simulation, which is the point: the implementation
introduced no behavior the probe had not already measured. Reports name the
boundary shape and dotted module, so a package run is never mistaken for a
flat one.

Survivor groups were then closed by laws the probe made possible, each
vocabulary word paying for itself in measured kills:

| Laws | Killed |
| --- | --- |
| original probe laws | 16 of 40 |
| plus the budget anchored to an external fact | 17 of 40 |
| plus the explicit tensor-parallel law (`depends` + `one_of`) | 21 of 40 |
| plus `as_dict`, weights/kv sharding, candidate completeness, concurrency | 35 of 40 |
| plus four boundary laws (`raises`, cross-domain, two `depends`-built) | **39 of 40** |

Every added law is provably load-bearing; the report attributes each new
kill to the law that made it. Two of the last four mutants were only
reachable by *constructing* the exact input — one sequence exactly fitting
the budget, and weights exactly filling it — which `depends` expresses by
deriving the second input from the first, hitting numeric boundaries a
uniform domain never lands on.

The one remaining mutant is a **proven equivalent**, not a survivor: in the
candidate filter, `size <= scale_up` versus `size < scale_up` cannot change
the result, because a second clause `size % scale_up == 0` always keeps
`size == scale_up`. It carries a committed ledger entry with that proof, so
the run is 39 killed, 0 unexplained survivors, 1 equivalent, exit 0 — every
mutant accounted for.

These artifacts are **retained and independently rerunnable** in
[`probes/kvfit/`](../probes/kvfit/README.md): the 15 laws, the equivalence
ledger, the recorded `tlaw.mutation/v1` report, and the pinned kvfit revision
(`f48a6abdef9795028332ae1cf2b74f091929e01c`) with the exact reproduction
command. The claim is published only because it can be reproduced, which is
the condition tlaw sets on its own documentation.

## Follow-up: the three deferred cases, probed

The original limitation named three deferred cases. Probing each showed two
were over-stated and one was a real, bounded gap now closed.

**Two-package laws already worked.** A law that imports two packages while
one module is mutated needs no new behavior: the mutated package is copied
and shadowed, and the second package stays installed and unmutated, which
is correct because only one module is under test. Verified by mutating
`alpha.core` while the laws also imported `beta`: canary killed, mutant
killed. Only the rare need to mutate two packages *simultaneously* remains
unaddressed, and no example asks for it.

**Compiled modules are refused honestly, by construction.** `mutate`
requires a `.py` implementation; a `.so`/`.pyd` extension fails that check
with a clear message. A source-level mutation tool cannot mutate compiled
code — this is a boundary, not a bug, and it is reported rather than
guessed.

**Namespace packages (PEP 420) were the real gap, now fixed.** Without an
`__init__.py` chain, the filesystem cannot say whether
`ns/shapes/geometry.py` is imported as `ns.shapes.geometry`,
`shapes.geometry`, or `geometry` — it depends on the import path. The
disambiguating fact is how the laws import the module, which tlaw now reads
statically (`_dotted_import_name`), matching the dotted tail against the
module's own path so a coincidental same-named import cannot mislead it.
The top namespace directory is then copied and shadowed exactly as a
regular package. A bare, undotted import keeps the flat boundary, and the
canary still proves the shadow took effect.
