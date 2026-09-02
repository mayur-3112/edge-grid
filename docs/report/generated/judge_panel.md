### Table 8.11 - Judge configurations against the two hard strategies

| Judge | Strategy | Recall | FPR | Precision (bal.) | Errors |
|:---|:---|---:|---:|---:|:---|
| qwen-27b | negate | 100% | 26% | 79% | 0/39 |
| qwen-27b | swap incorrect | 95% | 26% | 78% | 0/39 |
| qwen-27b | OVERALL | 98% | 26% | 79% | 0/99 |
| nemotron-120b | negate | 100% | 0% | 100% | 32/39 **unusable** |
| nemotron-120b | swap incorrect | 100% | 0% | 100% | 32/39 **unusable** |
| nemotron-120b | OVERALL | 100% | 0% | 100% | 83/99 **unusable** |
| minimax-m3 | negate | 100% | 16% | 86% | 0/39 |
| minimax-m3 | swap incorrect | 90% | 16% | 85% | 0/39 |
| minimax-m3 | OVERALL | 96% | 16% | 86% | 0/99 |
| ling-3-flash | negate | 100% | 0% | 100% | 36/39 **unusable** |
| ling-3-flash | swap incorrect | 100% | 0% | 100% | 35/39 **unusable** |
| ling-3-flash | OVERALL | 100% | 0% | 100% | 86/99 **unusable** |
| panel-majority | negate | 100% | 7% | 94% | 4/39 |
| panel-majority | swap incorrect | 95% | 7% | 93% | 5/39 |
| panel-majority | OVERALL | 97% | 7% | 94% | 5/99 |
| panel-unanimous | negate | 100% | 8% | 93% | 6/39 |
| panel-unanimous | swap incorrect | 95% | 8% | 92% | 7/39 |
| panel-unanimous | OVERALL | 97% | 8% | 93% | 7/99 |
| panel-any_fail | negate | 100% | 37% | 73% | 0/39 |
| panel-any_fail | swap incorrect | 95% | 37% | 72% | 0/39 |
| panel-any_fail | OVERALL | 98% | 37% | 73% | 0/99 |

*Generated from run `judge-panel-20260902T173031Z` at commit `f02eca6`. A configuration whose error rate exceeds 40% is marked unusable: its rates are computed over the few judgements that completed and carry no weight.*

