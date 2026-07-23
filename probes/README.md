# Probes

A probe applies tlaw to a real, external codebase to find bottlenecks and
earn features. Unlike `examples/`, which are self-contained and run in the
push gate on every commit, probes depend on separately-installed projects
and are reproduced on demand.

Each probe directory retains the inputs needed to independently rerun its
result — the laws, any equivalence ledger, the recorded report, and a pinned
revision of the target — so a claim tlaw makes about a probe in its
documentation can always be reproduced, not merely believed.

- [`kvfit/`](kvfit/README.md) — the package-boundary and vocabulary probe
  that reached 39 of 40 mutants killed on `kvfit.planner`, with the last
  mutant a proven equivalent.
- [`anef/`](anef/README.md) — the railway, async, composite-input, and
  redaction probe behind issue #1. It earned `record`, `returns`,
  `excludes`, await-aware `raises`, and the decision that effect-absence is
  a non-goal.
