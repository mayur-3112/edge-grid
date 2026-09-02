### Table 8.2 - Auction timing versus network size

| Nodes | Auctions | First bid (ms) | Last bid (ms) | Broadcast to award (ms) | Mesh forms (s) |
|:---|---:|---:|---:|---:|---:|
| 3 | 19 | 16.9 ± 6.8 | 21.3 ± 9.2 | 2,008 | 7.9 |
| 4 | 19 | 22.3 ± 14.1 | 32.6 ± 20.2 | 2,008 | 8.0 |
| 5 | 19 | 21.1 ± 9.4 | 36.7 ± 18.8 | 2,007 | 8.2 |

*Generated from run `exp2-auction-convergence-summary-20260902T110609Z` at commit `37378fd`. Broadcast-to-award is pinned by the fixed 2 s bid window; the bid arrival times are the scaling signal. All processes on one host.*

