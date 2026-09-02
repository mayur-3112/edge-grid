### Table 8.8 - Content-addressed weight distribution

| Artefact (bytes) | MiB | Cold fetch (ms) | Warm fetch (ms) | Speed-up | CID re-verified |
|:---|---:|---:|---:|---:|---:|
| 65536 | 0.06 | 6.6 | 0.38 | 17x | yes |
| 1048576 | 1.00 | 18.6 | 1.51 | 12x | yes |
| 4194304 | 4.00 | 40.2 | 0.43 | 94x | yes |
| 16777216 | 16.00 | 135.1 | 0.66 | 205x | yes |
| 50331648 | 48.00 | 317.5 | 0.35 | 896x | yes |

### Table 8.9 - Tamper detection, with an honest control

| Case | Outcome | Exception |
|:---|:---|:---|
| store serves other artefact | REJECTED | CIDMismatch |
| cached artefact bit flipped | REJECTED | nan |
| resolver on corrupted cache | REJECTED | ContentHashMismatch |
| control honest artefact | ACCEPTED | nan |

*Generated from run `weights-20260902T170213Z` at commit `75df836`. Verification recomputes the CID after download rather than trusting the daemon that served it.*

