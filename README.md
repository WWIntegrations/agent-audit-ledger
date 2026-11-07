# Agent Audit Ledger

A small local audit trail that answers two different questions: **is this chain internally consistent, and does it still contain the history an independent observer retained?**

The demo writes and reads two harmless files, records their measured hashes in SQLite, pins a checkpoint, and rebuilds two SQL projections from the events. It then changes a payload, removes a middle event, and removes the final event from separate copies. The last copy remains internally consistent after its head is adjusted. The retained checkpoint detects the missing history.

[Inspect the recorded experiment](docs/index.html) · [Design](docs/design.md) · [Threat model](docs/threat-model.md) · [Research](RESEARCH.md)

![Recorded ledger and replay inspector](docs/showcase.gif)

[Watch the 21-second interaction recording](docs/showcase.mp4) · [Verification receipt](docs/verification.md)

The animation presents the actual saved experiment. To use the interactive viewer locally:

```sh
python3 tools/serve.py
```

Open `http://127.0.0.1:3410/docs/index.html`. The viewer is self-contained and can also be opened directly as a local HTML file. GitHub's source viewer does not execute HTML.

## Run it

Python 3.11+ with SQLite 3.31+ is required. There are no runtime dependencies, model calls, credentials, or network services. Run from the repository without installing a package:

```sh
python3 -m unittest discover -s tests -v
python3 -m ledger_audit demo --out-dir /tmp/audit-ledger-demo-new
```

Use a new output directory. The CLI never overwrites a target. Its JSON result goes to stdout; runtime metadata and bounded errors go to stderr.

For a small ledger of your own:

```sh
python3 -m ledger_audit init --db /tmp/audit-example-new.sqlite
python3 -m ledger_audit append --db /tmp/audit-example-new.sqlite --event examples/start.json
python3 -m ledger_audit append --db /tmp/audit-example-new.sqlite --event examples/finish.json
python3 -m ledger_audit verify --db /tmp/audit-example-new.sqlite
python3 -m ledger_audit checkpoint --db /tmp/audit-example-new.sqlite --out /tmp/audit-checkpoint-new.json
python3 -m ledger_audit replay --db /tmp/audit-example-new.sqlite --out-db /tmp/audit-projection-new.sqlite
```

Repeating an identical event ID and input returns the original record. Reusing the ID for different content fails. An event after a run has finished fails. Appends and the ledger head commit together.

## What the evidence means

| Check | What it establishes |
| --- | --- |
| `chain: consistent`, `anchor: none` | The stored events, hashes, head and reducer agree with each other. Completeness is unknown. |
| `anchor: exact` | The current head matches the supplied independently retained checkpoint. |
| `anchor: prefix` | The checkpoint's prefix matches; the later tail has no newer independent anchor. |
| SQL replay | The saved events produce the shown state in a newly created SQLite database. No agent or external action is re-executed. |

Anchored verification requires both `--checkpoint FILE` and `--expected-checkpoint-digest DIGEST`. Retain that expected digest through a separately controlled channel **before** investigating a suspected change. Reading it automatically from a neighboring file would not create an independent witness. The demo simulates this separation by keeping the digest in its controller while modifying only its copied databases.

Hashes do not authenticate an actor, prove an artifact was possessed, establish trusted time, or stop the file owner rewriting data. An exact anchor does not prove the checkpoint was the newest one. The `actor`, timestamp and observed content digest are declarations.

## Saved artifacts, not invented metrics

`examples/recorded/` contains a completed local experiment: source ledger, two projection databases, the two measured files, checkpoint, export, tampered copies and report. Every run creates a fresh ledger ID and real timestamps; the raw SQLite files need not be byte-identical across runs.

```sh
python3 -c 'from pathlib import Path; from ledger_audit.recorded import validate_recorded; v=validate_recorded(Path("examples/recorded")); print(v["demo"]["replay_full"]["counts"])'
```

This validator opens the saved databases read-only. It compares actual SQL rows with the reducer, checks the report against the saved experiment, and checks file hashes and current source hashes. It never appends or reruns the demo. The manifest is unsigned: it establishes internal/source consistency, not independent proof that a particular person performed the experiment.

The inspector displays the two real saved projections. Its controls select evidence; they do not execute ledger commands in the browser. Downloads retain the original JSON bytes.

## Boundaries and verification

The ledger is deliberately bounded to 1000 events, 16 MiB JSON exports and 32 MiB SQLite files. It uses explicit SQLite transactions with DELETE journaling and EXTRA synchronization. Inputs use strict closed JSON formats and bounded regular-file reads. New outputs are published without replacing existing files.

The test suite covers actual concurrent processes, lost responses, process exits before/after COMMIT, a reader blocking COMMIT, atomic publication races, rehashed tampering, older anchors, import provenance, resource limits and recorded-artifact validation. Injected SQLite API failures test rollback handling; they are not tests of physical power loss or disk failure. See the threat model for the filesystem and single-operator assumptions.

Code and original fixtures are MIT licensed. The Python interpreter and SQLite retain their own licenses. No private operational workflows or production data are included.
