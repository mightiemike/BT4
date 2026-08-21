# Q54: MetricsApiService: bloom/topic amplification

## Question
Can an unprivileged attacker (smart-contract/query) abuse `MetricsApiService.getMetricsInfo` in `framework/src/main/java/org/tron/core/metrics/MetricsApiService.java` — where the attacker crafts topics so MetricsApiService.getMetricsInfo bloom/section work grows disproportionately — to break the invariant that MetricsApiService.getMetricsInfo work is bounded per event regardless of topic content, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/metrics/MetricsApiService.java` -> `MetricsApiService.getMetricsInfo`
- Entrypoint: emit/query events via MetricsApiService.getMetricsInfo
- Attacker controls: request/transaction/contract inputs to `MetricsApiService.getMetricsInfo` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts topics so MetricsApiService.getMetricsInfo bloom/section work grows disproportionately
- Invariant to test: MetricsApiService.getMetricsInfo work is bounded per event regardless of topic content
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: measure MetricsApiService.getMetricsInfo cost vs topic count
