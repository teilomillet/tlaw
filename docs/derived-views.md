# Derived views: design, gated behind evidence

Status: **design only, not implemented.** This records how the first
derived view should be built when it is built, and — more importantly —
what would make building it a mistake. Nothing here is usable syntax.

## Why this is deferred on purpose

A derived view summarises many laws into one picture: a dependency graph, a
contract card, a per-boundary confidence report. The temptation is strong,
because a green graph is satisfying. It is also the single most dangerous
thing tlaw could ship, for one reason recorded across the probes:

> A law can look protective while constraining nothing. The kvfit
> `verdict-justified-by-its-own-numbers` law passed against an implementation
> whose memory budget was mutated to nonsense, because it only compared the
> output to itself (docs/law-first-experiment.md, docs/package-boundary-probe.md).

A view that reads "this boundary has a passing law" and paints it green
would render that blind spot as protection. That is not a convenience bug;
it manufactures false trust, which is the exact opposite of the project's
purpose. `mutation-evidence.md` states the gate directly: a view **must not**
display a boundary as protected until its evidence includes independently
generated challenges.

So the rule is: a derived view may only report confidence it can source from
**mutation-graded** evidence. Everything else is `unknown`, loudly.

## The first view worth building: `tlaw map`

Not a graph. One flat report, one line per code boundary, three states:

```text
tlaw map examples/

  lawful          batch_packer/implementation.py   13/13 mutants killed
  lawful          score_weights/implementation.py  1 killed, 1 explained
  lawful          rescale/implementation.py         3 killed, 1 explained
  unknown         helpers/format.py                 no laws
```

State definitions, each provable and none guessed:

- **lawful** — the boundary has laws, `tlaw check` passes, and `tlaw mutate`
  killed or explained every runnable mutant. This is the only word that
  claims protection, and it is backed by adversarial evidence. An explained
  mutant remains visible as a committed unresolved-equivalent candidate; the
  explanation is accepted evidence, not an automatic proof of equivalence.
- **partial** — laws pass and mutation ran, but survivors remain (beyond
  committed equivalents). Honest middle: something is constrained, something
  is not.
- **unknown** — no laws, or laws but no mutation evidence. Never painted as
  safe. Existing behavior is not accepted behavior.

## What it must never do

- Never derive **lawful** from a passing `check` alone. Passing without
  mutation is "attempted, not refuted, at unmeasured strength" — that is
  `unknown` about strength, not `lawful`.
- Never invent a percentage of project correctness. The runnable mutant set
  is the challenges the engine generated, not the space of mistakes.
- Never hide a boundary that has no laws. A blank spot on the map is the most
  important thing the map shows.
- Never read across import edges and guess which imported module is
  implementation and which is specification. `tlaw diff` already refuses this
  guess; the map inherits the refusal.

## How it should be built (when it is)

1. Reuse existing evidence, do not recompute trust. `tlaw mutate` already
   emits a stable `tlaw.mutation/v1` JSON report per boundary; the map is a
   reader over those reports plus a `tlaw check` pass, not a new judge.
2. Probe it against a real multi-boundary repo first — this one, once it has
   several graded boundaries — and let that probe shape the columns, exactly
   as every feature so far was shaped by kvfit, retrain, and batch_packer.
3. Ship the three-state line report before any graph. A graph is a rendering
   of the same states and earns nothing until the states are trusted.

## Not in scope, and why

Dependency graphs, contract cards, change-impact reports, and cross-boundary
flow laws are all downstream of a trusted per-boundary map. They multiply the
surface where a wrong confidence claim can hide, so each needs its own probe
and its own evidence source. None is a fix; each is a project.
