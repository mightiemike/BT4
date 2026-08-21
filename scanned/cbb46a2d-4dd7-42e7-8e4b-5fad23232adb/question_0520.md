# Q520: RateLimiterContainer: preemptible limiter starvation

## Question
Can an unprivileged attacker (HTTP/gRPC gate) abuse `RateLimiterContainer.add` in `framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterContainer.java` — where the attacker occupies preemptible permits via RateLimiterContainer.add so legitimate traffic is starved — to break the invariant that permit accounting cannot be pinned by one client, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterContainer.java` -> `RateLimiterContainer.add`
- Entrypoint: hold long requests through RateLimiterContainer.add
- Attacker controls: request/transaction/contract inputs to `RateLimiterContainer.add` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: occupies preemptible permits via RateLimiterContainer.add so legitimate traffic is starved
- Invariant to test: permit accounting cannot be pinned by one client
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: open many slow requests and measure others' latency
