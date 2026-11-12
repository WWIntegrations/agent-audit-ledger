# Research and design decisions

Primary sources reviewed on 2026-11-06. This repository is not an integration with, or endorsement from, the organizations below.

## Event history and replay

[SQLite atomic commit](https://www.sqlite.org/atomiccommit.html) explains the filesystem and synchronization assumptions behind atomic transactions. The implementation uses a local SQLite database and process-crash checks. A killed process test does not establish hardware power-loss guarantees.

[SQLite transactions](https://www.sqlite.org/lang_transaction.html) and [isolation](https://www.sqlite.org/isolation.html) distinguish a write transaction from a successful commit. A reader can still cause a rollback-journal COMMIT to return BUSY after BEGIN IMMEDIATE. Append therefore includes validation and commit handling in its transaction boundary.

[SQLite synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous) documents EXTRA synchronization for DELETE journaling. The runtime checks the selected settings. An audit checkpoint in this project is a history reference, not a SQLite WAL checkpoint.

[Python sqlite3](https://docs.python.org/3.11/library/sqlite3.html) provides explicit transactions, parameter binding, database backup and limits. [SQLite security guidance](https://www.sqlite.org/security.html) motivates bounded reads and defensive handling of altered local databases.

[RFC 9162](https://www.rfc-editor.org/rfc/rfc9162.html), especially sections 8 and 11, describes comparison with previously known states in a different, signed transparency system. This project implements a simpler unsigned hash chain. It does not implement Certificate Transparency, Merkle proofs, signatures, gossip or an independent network witness.

The important distinction is between internal consistency and an independently retained reference. A rewritten or shortened history may remain internally consistent. An expected checkpoint digest can protect its known prefix; it cannot establish the truth of an event or protect an unknown later tail.

Replay materializes a new SQLite projection from recorded events. It does not execute the original agent actions. The static viewer displays validated recorded artifacts and cannot attest to the honesty of their producer.

## Visual reference and assets

The measured visual reference is [Slush](https://slush.app/): a black outer frame, rounded pale blue field, oversized condensed type, floating objects and pill navigation. The implementation uses its own ledger artwork and evidence screens. Differences in licensed typography, geometry and animation timing mean this is not a certified pixel-identical reproduction.

Anton is distributed under the SIL Open Font License. The font and its complete license are in `docs/fonts/anton/`. Body text uses system fonts. The book icon, link artwork and diagram are original project assets. No Slush logo, commercial font, product screen, video or model is included.

The architecture diagram describes the actual append, verification and projection boundaries. Animation is presentation of recorded evidence, not live execution or an integrity guarantee. The viewer provides motion controls and a reduced-motion presentation.
