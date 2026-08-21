# Q523: QpsRateLimiterAdapter: rate-limit key bypass

## Question
Can an unprivileged attacker (HTTP/gRPC gate) abuse `QpsRateLimiterAdapter.tryAcquire` in `framework/src/main/java/org/tron/core/services/ratelimiter/adapter/QpsRateLimiterAdapter.java` — where the attacker varies the field QpsRateLimiterAdapter.tryAcquire keys on (IP header, method name) to escape the per-key QPS bucket and flood a costly endpoint — to break the invariant that rate limiting binds to an attacker-immutable key and cannot be shed, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/ratelimiter/adapter/QpsRateLimiterAdapter.java` -> `QpsRateLimiterAdapter.tryAcquire`
- Entrypoint: flood requests through QpsRateLimiterAdapter.tryAcquire varying spoofable headers
- Attacker controls: request/transaction/contract inputs to `QpsRateLimiterAdapter.tryAcquire` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: varies the field QpsRateLimiterAdapter.tryAcquire keys on (IP header, method name) to escape the per-key QPS bucket and flood a costly endpoint
- Invariant to test: rate limiting binds to an attacker-immutable key and cannot be shed
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: send bursts with rotating X-Forwarded-For and assert limiter holds
