### Performance Results

| Scenario | Buyers + Sellers | Clients | Avg Response Time (s) | Avg Throughput (ops/s) |
|---|---:|---:|---:|---:|
| 1 | 1 + 1 | 2 | 0.000419 | 3860.43 |
| 2 | 10 + 10 | 20 | 0.007312 | 2611.08 |
| 3 | 100 + 100 | 200 | 0.167809 | 1177.09 |

* Latency grows sharply with load (~0.42 ms -> 7.3 ms -> 168 ms), indicating queueing and contention under heavier parallelism
* Throughput drops as concurrency increases (3860 -> 2611 -> 1177 ops/s), suggesting the system saturates before 200 clients and spends more time waiting on shared resources
* Scenario 2 already begins to show some saturation effects, and scenario 3 likely exceeds efficient parallelism, so additional clients increase latency and slow down the overall system a whole lot more
