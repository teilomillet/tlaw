# anef probe: the verification-compilation refactor

This directory retains the artifacts behind [issue #1][issue]: the five laws
written against a real, in-flight refactor in anef, used as the driving case
for five tlaw capability gaps, plus a sixth that a later probe earned. anef
is **read, never modified** — the laws live here, import anef's installed
functions, and run under anef's own environment.

[issue]: https://github.com/teilomillet/tlaw/issues/1

## What is here

- `laws.py` — the five laws from the issue, in order, then Law 6.
- `report.json` — the recorded `tlaw.mutation/v1` report for the run below.

## The target

anef's `backend/verification/compose.py` currently inlines an
`interpret -> resolve` block that the refactor extracts into a new
`compile_verification`. **That function does not exist yet.** `compiled(...)`
in `laws.py` is that block exactly as `compose` writes it today, calling
anef's real `interpret`, `resolve`, and real `_CALLER_RESOLVE_CODES`
attribution set. No anef behavior is reimplemented; only the orchestration is
mirrored, so every law can be repointed at `compile_verification` without
changing a claim. That is the parity the refactor needs, and it is the
honest limit of this probe: it pins the block *before* extraction.

Pinned at anef commit `a2b45e9` (`Add document classifier guardrail`), branch
`teim/service-refactor`.

## Reproduce

anef is not installed as a package; its pytest configuration puts the
repository root on the path. `tlaw check` runs pytest from the law file's own
directory, so the root is supplied through `PYTHONPATH` instead. From the
tlaw repository root:

```console
PYTHONPATH=/path/to/anef_mvp:$PWD uv run --project /path/to/anef_mvp \
    python -m tlaw check probes/anef/laws.py --explain
```

Expected: `6 passed`.

`python -m tlaw` rather than the `tlaw` script, and `PYTHONPATH` including
`$PWD`, because anef declares `tlaw>=0.0.3` as a dependency and would
otherwise resolve the released version instead of this working tree.

## The laws and the gaps they earned

| Law | Claim | Gap it earned |
| --- | --- | --- |
| 1 | A valid request compiles to exactly the identity and ordered field selection it declared. | **G1** `tlaw.record` — the request is a nested composite; without it this law degraded to a curated `one_of` of seeds. |
| 2 | An uninterpretable payload refuses as the caller's mistake, and the resolver is never reached. | **G2** `tlaw.returns`; **G3** decided a non-goal (see below). |
| 3 | A field ID absent from the catalog stays caller-attributable. | **G2** `tlaw.returns` — the failure names which branch came back. |
| 4 | An adapter fault is server-attributable and carries no hostname, DSN, or key path. | **G4** `tlaw.excludes` — and **G2**, because at this boundary the fault is an `Err`, not a raise. |
| 5 | Resolver cancellation propagates rather than being reclassified. | **G5** await-aware `tlaw.raises`. |
| 6 | A server check on a selected *and* claimed field resolves. | `depends` inside `record`, scoped to sibling fields (see below). |

Two findings sharpened the issue's own description:

- **Law 4 is not a `raises` law.** The issue assumed the adapter exception
  surfaces as a raise. anef's `_lookup` converts it to
  `Err(PROFILE_UNAVAILABLE)` precisely so storage details cannot reach a
  reply, so the law needed `returns` plus `excludes`, not `raises`.
- **Law 5 was unwritable, not merely awkward.** The issue expected friction
  from an `asyncio.run` shim. In fact `asyncio.CancelledError` is a
  `BaseException`, which `tlaw.raises` refused outright, so no spelling of
  the law existed.

## G3, and why effect-absence is a non-goal

tlaw sequences the calls you make; it does not spy on collaborators. Law 2's
second half is expressed by injecting `NeverCalledResolver`, which is
ordinary Python.

The probe found the real trap. anef's `_lookup` catches `Exception` to keep
storage details out of replies — correct behavior that also **absorbs an
`AssertionError` raised by an injected double**. With an `AssertionError`
signal, feeding a *valid* payload to Law 2 made the law fail for the wrong
reason (wrong attribution) and never reported that the resolver had been
reached. The double therefore signals with `ResolverWasCalled`, a
`BaseException` subclass, which no `except Exception` can swallow.

That caveat is the substance of the G3 decision: the pattern works, but only
when the signal outlives the target's error handling.

## Mutation evidence

```console
PYTHONPATH=/path/to/anef_mvp:$PWD uv run --project /path/to/anef_mvp \
    python -m tlaw mutate probes/anef/laws.py \
    /path/to/anef_mvp/backend/verification/profile/resolve.py
```

Recorded in `report.json`:

```text
Boundary: package (backend.verification.profile.resolve)
Canary: KILLED (laws observe the mutated module)
Killed 34 of 72 runnable generated mutations.
Survivors: 38
```

**These laws do not pin `resolve` as a whole, and this probe does not claim
they do.** Every survivor sits on a path the laws' declared domains never
enter: duplicate document IDs and `document_ids.add` (the request domain
generates exactly one document), `SOURCE_RESERVED` and `SOURCE_COLLISION`
(no colliding claim source is generated), field validation for empty,
duplicate, over-long, or unsupported selections (every generated field is a
valid supported `text` kind), and profile consistency (the injected
resolvers always return a well-formed profile matching the key).

Adding Law 6 created nine new kills inside `_checks`, which the first five laws
could not reach at all. It also **cost** one kill: raising `max_checks` from
`0` to `2` means no generated request now sits exactly on the check limit, so
the `total_checks > max_checks` boundary mutant survives where it previously
died. Nine new kills minus that lost kill moved the result from 26/72 to 34/72,
a net gain of eight. A declared domain decides what a law can observe, in both
directions, and the report is left to say so rather than being tuned to look
better.

That is the intended reading of a mutation report: it counts the generated
challenges these laws answered, and nothing else.

## Law 6, and the relation that earned sibling `depends`

Law 6 is not one of the issue's five. It exists because trying to reach
anef's server-check path proved the composite domain was still too weak.

Reaching `_checks` requires the profile's check, the caller's field
selection, and the claim to name the **same** field. With independent
domains that is unreachable: a check on `person_name` against a request
selecting only `birth_date` fails with `CHECK_FIELD_MISSING`, correctly, and
the law can never observe the path it was written for.

`record` now accepts a `depends(...)` field naming **sibling fields of the
same record**, so the check co-varies with the selection by construction:

```python
CheckedRequests = tlaw.record(
    fields=tlaw.one_of((("person_name",), ("birth_date",), ...)),
    checked=tlaw.depends(lambda fields: tlaw.one_of(fields)),
)
```

Sibling scope is the whole namespace a record's `depends` can see. Naming a
law parameter from inside a record is refused when the record is defined,
because one word resolving against whichever scope happened to contain the
name would be a guess about which contract the author meant.

Fixing the first relation immediately surfaced a second one underneath —
`CHECK_CLAIM_MISSING`, because the claim must carry the checked field too —
which is why `request_for` takes `claimed`. Law 6 passes only with both
relations satisfied, and nothing is filtered to get there.

## Why this lives in `probes/`, not `examples/`

The examples are self-contained and run in the push gate on every commit.
This probe depends on an external, separately-installed, private project, so
it cannot run in the gate. It is retained as evidence and reproduced on
demand.
