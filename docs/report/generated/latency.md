### Table 8.1 - Time to first token

| Condition | N | Mean (ms) | Median (ms) | p95 (ms) | Tokens/s |
|:---|---:|---:|---:|---:|---:|
| Warm | 20 | 609.6 | 587.9 | 723.6 | 12.86 |
| Cold (paired) | 5 | 7,963.8 | 7,809.9 | 8,231.4 | - |
| Warm (paired) | 5 | 653.7 | 600.8 | 789.9 | - |

*Generated from run `inference-benchmark-20260902T120811Z` at commit `37378fd`. Model qwen3-vl:2b-instruct on a CPU node with no accelerator. 20 of 20 warm trials fell below one second.*

