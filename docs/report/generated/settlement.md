### Table 8.5 - Gas per on-chain operation

| Operation | Gas |
|:---|---:|
| proveDataMismatch | 221,353 |
| recordCommitment | 184,028 |
| submitVerdict | 158,721 |
| openEscrow | 126,933 |
| release | 78,219 |
| setValidator | 47,827 |
| stake | 35,402 |
| withdraw:marketplace | 32,317 |

### Table 8.6 - Resolution path taken per job

| Job | Resolution | Final state | Slashed (GRID) |
|:---|:---|:---|---:|
| `job-honest-9` | challenge window elapsed | settled | 0.0000 |
| `job-fraud-14` | data mismatch proof | slashed | 0.0500 |
| `job-verdict-18` | validator verdict | slashed | 0.0500 |

*Generated from run `settlement-onchain-20260902T120752Z` at commit `37378fd`. Local EVM chain, chainId 31337. Gas is reported in units: this chain has no gas price, and converting with an invented one would be a fabrication.*

