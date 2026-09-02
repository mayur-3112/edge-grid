### Table 8.3 - Fraud detection by corruption strategy

| Strategy | TP | FP | TN | FN | Err | Precision | Precision (bal.) | Recall | F1 |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| hallucinate_entity | 19 | 0 | 20 | 1 | 0 | 100.0% | 100.0% | 95.0% | 97.4% |
| negate | 6 | 0 | 20 | 14 | 0 | 100.0% | 100.0% | 30.0% | 46.2% |
| random_topic | 20 | 0 | 20 | 0 | 0 | 100.0% | 100.0% | 100.0% | 100.0% |
| swap_incorrect | 7 | 0 | 20 | 13 | 0 | 100.0% | 100.0% | 35.0% | 51.8% |
| **OVERALL** | 52 | 0 | 20 | 28 | 0 | 100.0% | 100.0% | 65.0% | 78.8% |

*Generated from run `verification-20260902T121801Z` at commit `37378fd`. Precision is reported both as measured on the natural 1:4 honest-to-fraud design and class-balanced; the raw figure alone overstates the judge. Judge errors are counted separately and never folded into failures.*

