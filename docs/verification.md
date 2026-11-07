# Verification receipt

Verified on 2026-11-07. This describes the checked repository state, not a guarantee about other datasets, machines or later changes.

| Check | Observed result |
| --- | --- |
| Python core tests | 36 passed, 8.404 seconds on the local verification run |
| Interface and builder tests | 17 passed, 312.345 ms on the local verification run |
| Browser checks | 783 passed across six contexts |
| Browser page errors | 0 |
| External HTTP requests from standalone viewer | 0 |
| Recorded HTML build | Two builds identical byte-for-byte |
| Independent source review | Approved after correcting publication recovery immediately after hardlink creation |

Browser contexts were 1440x900, 390x844, 320x740, 768x1024, reduced motion at 390x844, and Canvas unavailable at 390x844. Checks compared all four displayed events and their payloads/hashes, all three altered copies with both anchor perspectives, both SQL projections and rows, and both measured file records with the original recorded data. Each of the four JSON downloads matched its saved original bytes. Menu and dialog focus, native previous/next controls, pause/resume, dynamic reduced motion, context loss and horizontal layout were exercised.

A first browser run found a 0.844px text overflow at 320px. The narrow-screen heading size was corrected and the entire six-context run passed afterward. This was a real layout failure, not a skipped check.

Core tests include real subprocess exits around COMMIT and file publication, concurrent writers, idempotent retries, a reader blocking COMMIT, and reading a complete output after a crash immediately following hardlink creation. The temporary name may survive that crash; the complete regular file remains readable under the documented trusted-directory assumption. Fault injection also covers SQLite API errors; these tests do not simulate hardware power failure.

The source HTML checked in the browser and used for the recording has SHA256:

`b78a6bfd3ab2410b12bad977d39975a9123a00c5e36ed418aff67319b370a03a`

The recording contains actual browser interactions at 1440x900, 25 fps, 543 frames, 21.72 seconds. Asset hashes are in [media-manifest.json](media-manifest.json). The browser selects recorded evidence; it does not perform fresh SQLite operations. The diagram and custom404 were separately checked in a browser on desktop and mobile.

The experiment contains four events and two measured files totalling104 bytes. The retained checkpoint identifies the four-event prefix; the shorter replay uses three events. Unsigned manifests and checkpoints establish internal consistency under the stated trust boundary, not identity, trusted time or event truth. Full visual equivalence with the design reference is not established; the artwork and interactions are original, and Anton/system fonts replace licensed reference fonts.

To repeat core and interface checks:

```sh
python3 -B -m unittest discover -s tests -v
npm ci
npm test
npm run bundle
python3 -B tools/build_showcase.py --from-recorded
```

The Node dependencies are build-only. The CLI and saved-artifact validator use the Python standard library.
