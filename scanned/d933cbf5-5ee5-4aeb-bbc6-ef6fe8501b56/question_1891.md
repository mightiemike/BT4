# Q1891: FullNodeHttpApiService: unbounded response build

## Question
Can an unprivileged attacker (HTTP servlet) abuse `FullNodeHttpApiService.addServlet` in `framework/src/main/java/org/tron/core/services/http/FullNodeHttpApiService.java` — where the attacker supplies parameters to FullNodeHttpApiService.addServlet that force materialization of an unbounded result set or oversized JSON with no server-side cap — to break the invariant that every public read caps result size and iteration independent of client input, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/FullNodeHttpApiService.java` -> `FullNodeHttpApiService.addServlet`
- Entrypoint: HTTP request to FullNodeHttpApiService.addServlet with maximal range/count params
- Attacker controls: request/transaction/contract inputs to `FullNodeHttpApiService.addServlet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies parameters to FullNodeHttpApiService.addServlet that force materialization of an unbounded result set or oversized JSON with no server-side cap
- Invariant to test: every public read caps result size and iteration independent of client input
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: time and heap-profile the handler under an extreme count parameter
