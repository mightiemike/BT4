# Q3849: MetricsApiService: bloom/topic amplification

## Question
Can an unprivileged attacker (smart-contract/query) abuse `MetricsApiService.getMetricProtoInfo` in `framework/src/main/java/org/tron/core/metrics/MetricsApiService.java` — where the attacker crafts topics so MetricsApiService.getMetricProtoInfo bloom/section work grows disproportionately — to break the invariant that MetricsApiService.getMetricProtoInfo work is bounded per event regardless of topic content, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/metrics/MetricsApiService.java` -> `MetricsApiService.getMetricProtoInfo`
- Entrypoint: emit/query events via MetricsApiService.getMetricProtoInfo
- Attacker controls: request/transaction/contract inputs to `MetricsApiService.getMetricProtoInfo` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts topics so MetricsApiService.getMetricProtoInfo bloom/section work grows disproportionately
- Invariant to test: MetricsApiService.getMetricProtoInfo work is bounded per event regardless of topic content
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: measure MetricsApiService.getMetricProtoInfo cost vs topic count
