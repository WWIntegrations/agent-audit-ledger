# Design

The source of truth is an ordered set of closed JSON events in SQLite. A second table stores the header and current head. There is no mutable live projection that could drift from an append.

## Events and canonical bytes

Three event variants suffice:

- `run_started`: a new run ID, an actor label and a declared input digest.
- `artifact_recorded`: a new artifact ID within an active run, content digest and byte count.
- `run_finished`: success or failure for an active run.

Canonical bytes use Python JSON with sorted keys, ASCII escapes and compact separators, without a trailing newline. Arrays retain order. Digests are SHA256 over `domain + LF + canonical bytes`. The header is included in every event preimage, binding records to a ledger ID and reducer version. JSON files add one newline outside the hash preimage.

There are no floats, exponent numbers, negative integers, duplicate keys, BOMs or surrogate code points. Integers are bounded to the exactly representable JavaScript integer range, but the browser still downloads original bytes instead of regenerating evidence with `JSON.stringify`. Logical IDs are ASCII names, never paths to open.

`event_hash` covers header, sequence, event ID, type, payload, recorded timestamp and previous hash. It excludes itself. The genesis digest covers the header. Time does not determine order; the sequence does.

## Append and failure ordering

Each append opens its own SQLite connection and uses `BEGIN IMMEDIATE`. It verifies the entire bounded ledger in the same transaction, then checks idempotency before evaluating the proposed new transition. This allows a retry of an already committed finish event even though the run is now finished.

A new event INSERT and head UPDATE commit together. Identical event ID/input returns the stored record, preserving its original sequence and timestamp. A different input under that ID fails. There is no automatic retry for I/O failures. If the response is lost after COMMIT, retrying the identical input resolves the stored state.

SQLite settings are read back on every connection: foreign keys enabled, trusted schema disabled, mmap disabled, synchronous EXTRA, bounded busy timeout, and DELETE journaling. macOS also uses fullfsync. A fixed schema rejects additional tables, triggers, views and indexes before domain reads. Length, SQL length, page count, file size and SQL progress budgets bound the local workload.

A reader can still block COMMIT in rollback-journal mode. The process test verifies this specific path and confirms rollback, rather than assuming `BEGIN IMMEDIATE` eliminates every BUSY result. Process-crash tests interrupt a child after INSERT, after the head UPDATE and after COMMIT. They do not emulate power failure.

## Checkpoints and portable transfer

A checkpoint is a header, sequence and head hash from a fully verified read snapshot. Validation also requires an expected checkpoint digest supplied separately. Neither filenames nor filesystem modification dates select a trusted checkpoint.

Export reads one verified snapshot. JSON import validates all events and transitions before creating a new database. Imported copies always record `origin=imported` and the digest of their input export. Re-export keeps event hashes but changes provenance; identical complete exports are not expected across this transition. Provenance is unsigned and does not authenticate the source.

## Two independent representations of replay

Replay validates the entire source snapshot even when the requested projection ends earlier. It applies events up to the selected sequence through actual INSERT/UPDATE statements into a new database containing `runs`, `artifacts` and `projection_meta`.

An independent pure reducer builds the expected logical state. The SQL rows are read in canonical order and compared with that state. The database is then closed and reopened before its result and counts are returned. Replaying a zero prefix yields an empty projection. Replaying before finish yields an active run; full replay yields the recorded outcome.

New database and JSON targets are built in private temporary files in the destination directory. After commit/validation/close and file synchronization, a hard link publishes the complete target without clobbering an existing name. Directory synchronization follows. A crash after publication can leave a complete target even if the caller did not receive success. The final target remains readable even if a crash leaves the temporary hard-link name alongside it. Readers accept regular hard links in the trusted directory. Nothing automatically removes that final target.

## Recorded experiment interface

`ledger_audit.demo.make_demo(new_directory)` performs the experiment and writes `demo-result.json` plus `recorded-manifest.json`. The manifest lists hashes of the fixed artifact files and runtime Python sources. It does not sign them.

`ledger_audit.recorded.validate_recorded(directory)` uses read-only SQLite connections and returns:

```text
{
  format: "audit-inspector-v1",
  demo: DemoResult,
  export: Export,
  checkpoint: Checkpoint,
  projections: {
    prefix: {meta: ProjectionMeta, state: CanonicalState},
    full: {meta: ProjectionMeta, state: CanonicalState}
  },
  manifest: RecordedManifest
}
```

The validator checks source binding, actual file hashes, export/source equality, actual SQL projection rows, measured artifacts and all three tampered copies. It recomputes verification results from saved evidence and compares the saved report. It does not execute the demo or claim an external witness. The showcase is a viewer over this validated result.
