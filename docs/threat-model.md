# Threat model and limits

## Protected property

A correctly running verifier detects a changed or shortened recorded prefix when it is compared with an independently retained checkpoint digest. Without such an anchor, verification establishes only internal consistency.

The adversary in the experiment may edit a closed ledger database or JSON export, remove events, and recompute the chain and head. They cannot replace the running verifier or the expected checkpoint digest held by the independent observer. Correct concurrent writer processes are supported.

## Explicit non-guarantees

- No signatures, MACs, trusted clock, authenticated actors or proof that a described action happened.
- No guarantee that every real-world event was recorded. A checkpoint cannot reveal omissions before capture.
- No protection for an unanchored tail beyond an older checkpoint. Removing that tail can still pass prefix verification.
- No protection if the adversary can replace both the ledger and all purported independent evidence.
- No protection against an administrator modifying the running process, OS, or active file identity during operations.
- No distributed writers, network filesystems, external services or execution of saved agent/tool instructions.
- No complete simulation of physical power loss, failing storage hardware or every filesystem/VFS implementation.

An actor is a label supplied by the caller. An artifact digest is a recorded observation. The demo measures its two local files, but a generic `artifact_recorded` input is not a proof of possession. Artifact IDs are not file paths.

## Local storage assumptions

Use a trusted local directory under one operator. JSON reads reject final-component symlinks and non-regular files, use `O_NONBLOCK|O_NOFOLLOW`, read bounded bytes through that same FD and check metadata again. This rejects FIFOs without waiting for a writer. Regular hard links are accepted: a crash immediately after publication can leave both the final and temporary names pointing at the same complete file. Do not operate on multiple database aliases concurrently. This does not defend against every active same-UID replacement race.

SQLite is not treated as a general-purpose import format. The application expects its exact fixed schema, rejects extra executable schema objects, disables trusted schema and extension loading, and limits SQL work. JSON is the supported interchange format. SQLite recovery may write a hot journal back to an existing database even for logical verification. The recorded-artifact viewer instead opens read-only databases and refuses a leftover rollback journal.

Resource caps are limits, not a memory sandbox. SQLite main files and journals are bounded individually; transient disk usage includes temporary output files. A progress handler limits SQL VM work, not a hard wall-clock deadline for all filesystem I/O.

## Deliberate negative controls

The local demo changes only its new owned copies:

1. A changed content digest without rehashing fails hash verification.
2. A removed middle event fails sequence verification.
3. A removed final event with adjusted metadata passes unanchored consistency, but fails against the retained full checkpoint.

A separate test rewrites the full chain after changing content. It passes unanchored verification and fails the independently pinned anchor. Another test removes only the tail after an older checkpoint and confirms the limitation: the older anchored prefix still passes.

Saved manifests and reports are unsigned. The recorded validator detects disagreement between sources, files, rows and report. Someone able to replace all of them consistently can create a different valid dataset. The public inspector therefore presents recorded evidence and its scope; it does not claim authenticated execution or a trusted witness.
