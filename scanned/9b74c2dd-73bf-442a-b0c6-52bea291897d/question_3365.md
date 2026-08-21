# Q3365: LiteFnQueryHttpFilter: lite-node query gate bypass

## Question
Can an unprivileged attacker (HTTP/gRPC gate) abuse `LiteFnQueryHttpFilter.doFilter` in `framework/src/main/java/org/tron/core/services/filter/LiteFnQueryHttpFilter.java` — where the attacker crafts a request that LiteFnQueryHttpFilter.doFilter fails to classify as heavy, letting a disabled/gated query execute — to break the invariant that the query gate rejects every heavy path uniformly regardless of encoding, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/filter/LiteFnQueryHttpFilter.java` -> `LiteFnQueryHttpFilter.doFilter`
- Entrypoint: request a gated query variant through LiteFnQueryHttpFilter.doFilter
- Attacker controls: request/transaction/contract inputs to `LiteFnQueryHttpFilter.doFilter` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a request that LiteFnQueryHttpFilter.doFilter fails to classify as heavy, letting a disabled/gated query execute
- Invariant to test: the query gate rejects every heavy path uniformly regardless of encoding
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: try casing/alias variants of a blocked method
