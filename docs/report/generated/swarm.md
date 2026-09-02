### Table 8.10 - Auction timing across container network namespaces

| Injected RTT (ms) | Nodes | Auctions | First bid (ms) | Last bid (ms) | Mesh forms (s) |
|:---|---:|---:|---:|---:|---:|
| 0 | 3 | 1 | 6.0 | 7.0 | 5.9 |
| 10 | 3 | 2 | 44.5 | 51.0 | 12.0 |
| 25 | 3 | 2 | 71.0 | 73.5 | 14.0 |
| 50 | 3 | 2 | 114.0 | 117.5 | 14.7 |

*Each node is a container with its own network namespace and a distinct address on a bridge, so peers no longer share a loopback interface. This is not a LAN deployment: one kernel, no physical NIC, no wide-area path. Latency is injected with `tc netem`.*

