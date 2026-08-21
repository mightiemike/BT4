# Q3196: GetMarketOrderListByPairServlet: unbounded response build

## Question
Can an unprivileged attacker (HTTP servlet) abuse `GetMarketOrderListByPairServlet.doGet` in `framework/src/main/java/org/tron/core/services/http/GetMarketOrderListByPairServlet.java` — where the attacker supplies parameters to GetMarketOrderListByPairServlet.doGet that force materialization of an unbounded result set or oversized JSON with no server-side cap — to break the invariant that every public read caps result size and iteration independent of client input, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/GetMarketOrderListByPairServlet.java` -> `GetMarketOrderListByPairServlet.doGet`
- Entrypoint: HTTP request to GetMarketOrderListByPairServlet.doGet with maximal range/count params
- Attacker controls: request/transaction/contract inputs to `GetMarketOrderListByPairServlet.doGet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies parameters to GetMarketOrderListByPairServlet.doGet that force materialization of an unbounded result set or oversized JSON with no server-side cap
- Invariant to test: every public read caps result size and iteration independent of client input
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: time and heap-profile the handler under an extreme count parameter
