# Q2564: MetricsApiService: node info disclosure

## Question
Can an unprivileged attacker (smart-contract/query) abuse `MetricsApiService.getMetricProtoInfo` in `framework/src/main/java/org/tron/core/metrics/MetricsApiService.java` — where the attacker queries MetricsApiService.getMetricProtoInfo to read node internals that aid a further in-scope attack — to break the invariant that MetricsApiService.getMetricProtoInfo exposes no sensitive internal state to anonymous callers, leading to: Information disclosure (in-scope only if it enables impact)?

## Target
- File/function: `framework/src/main/java/org/tron/core/metrics/MetricsApiService.java` -> `MetricsApiService.getMetricProtoInfo`
- Entrypoint: anonymous query to MetricsApiService.getMetricProtoInfo
- Attacker controls: request/transaction/contract inputs to `MetricsApiService.getMetricProtoInfo` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: queries MetricsApiService.getMetricProtoInfo to read node internals that aid a further in-scope attack
- Invariant to test: MetricsApiService.getMetricProtoInfo exposes no sensitive internal state to anonymous callers
- Expected Immunefi impact: Information disclosure (in-scope only if it enables impact)
- Fast validation: assert MetricsApiService.getMetricProtoInfo response omits sensitive fields
