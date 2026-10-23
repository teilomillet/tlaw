# tlaw documentation index

This folder mixes three kinds of writing. Know which one you are reading
before trusting a claim:

| Document | Kind | What it is for |
| --- | --- | --- |
| [current tlaw behavior](current-behavior.md) | **implemented behavior** | The authoritative description of what the code in this repository does today, and what it deliberately does not do. If another page disagrees with this one, this one is right. |
| [generated mutation evidence](mutation-evidence.md) | implemented rules | The evidence loop `tlaw mutate` enforces: canary, obligations, exact replay, timeout confirmation, equivalents, honest outcomes. |
| [core evidence](core-evidence.md) | maintained evidence | The repository's layers of self-evidence (tests, CrossHair, `self_laws/`, `selflaws/`, `scripts/core-evidence`) and the limits of each layer. |
| [acceptance and evidence](acceptance-and-evidence.md) | working design detail | Git as the acceptance ledger, `tlaw diff`, replay, and verdict discipline. Implemented parts are marked; some sections are stated design targets, not current output. |
| [interface direction](interface-direction.md) | product direction | The reasoning behind the ladder and where the interface should go next. Implemented parts are marked, but it also contains future direction; `current-behavior.md` remains the executable truth. |
| [derived views](derived-views.md) | **design only, not implemented** | Why project-wide graphs or confidence views are deferred until mutation evidence can source them. Nothing here is usable syntax. |
| [kvfit probe](kvfit-probe.md), [law-first experiment](law-first-experiment.md), [package-boundary probe](package-boundary-probe.md) | dated probe records | What applying tlaw to real code found, kept reproducible. They describe the version under test, not necessarily today's behavior. |

Probe inputs and recorded reports live in [`probes/`](../probes/README.md).
Examples that run in the push gate are under [`examples/`](../examples);
the front door is the [root README](../README.md); contribution and release
rules are in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Keeping these pages honest

These documents are written by hand and have no generator, so they can drift.
When a change alters user-visible behavior, update
[current behavior](current-behavior.md) in the same commit. Its maintenance
note lists the executable tests that pin its main claims. The first mechanical
guard over the documentation itself is `tests/test_docs_links.py`: it walks
every Markdown file in the repository and fails when a relative link, a
self-repository URL, or a heading anchor points nowhere.
