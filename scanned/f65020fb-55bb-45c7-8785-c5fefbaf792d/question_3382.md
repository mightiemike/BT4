# Q3382: HttpService: preemptible limiter starvation

## Question
Can an unprivileged attacker (HTTP/gRPC gate) abuse `HttpService.start` in `framework/src/main/java/org/tron/common/application/HttpService.java` — where the attacker occupies preemptible permits via HttpService.start so legitimate traffic is starved — to break the invariant that permit accounting cannot be pinned by one client, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/common/application/HttpService.java` -> `HttpService.start`
- Entrypoint: hold long requests through HttpService.start
- Attacker controls: request/transaction/contract inputs to `HttpService.start` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: occupies preemptible permits via HttpService.start so legitimate traffic is starved
- Invariant to test: permit accounting cannot be pinned by one client
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: open many slow requests and measure others' latency
