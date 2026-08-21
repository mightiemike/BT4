# Q389: MetricsApiService: node info disclosure

## Question
Can an unprivileged attacker (smart-contract/query) abuse `MetricsApiService.getMetricsInfo` in `framework/src/main/java/org/tron/core/metrics/MetricsApiService.java` — where the attacker queries MetricsApiService.getMetricsInfo to read node internals that aid a further in-scope attack — to break the invariant that MetricsApiService.getMetricsInfo exposes no sensitive internal state to anonymous callers, leading to: Information disclosure (in-scope only if it enables impact)?

## Target
- File/function: `framework/src/main/java/org/tron/core/metrics/MetricsApiService.java` -> `MetricsApiService.getMetricsInfo`
- Entrypoint: anonymous query to MetricsApiService.getMetricsInfo
- Attacker controls: request/transaction/contract inputs to `MetricsApiService.getMetricsInfo` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: queries MetricsApiService.getMetricsInfo to read node internals that aid a further in-scope attack
- Invariant to test: MetricsApiService.getMetricsInfo exposes no sensitive internal state to anonymous callers
- Expected Immunefi impact: Information disclosure (in-scope only if it enables impact)
- Fast validation: assert MetricsApiService.getMetricsInfo response omits sensitive fields
