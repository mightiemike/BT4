# Q3061: GetTransactionSignWeightServlet: uncaught exception 500-loop

## Question
Can an unprivileged attacker (HTTP servlet) abuse `GetTransactionSignWeightServlet.doPost` in `framework/src/main/java/org/tron/core/services/http/GetTransactionSignWeightServlet.java` — where the attacker sends malformed body to GetTransactionSignWeightServlet.doPost that throws an unhandled exception path capable of exhausting threads or leaking a stack trace — to break the invariant that malformed input yields a bounded 4xx, never an unhandled fatal or secret leak, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/GetTransactionSignWeightServlet.java` -> `GetTransactionSignWeightServlet.doPost`
- Entrypoint: malformed HTTP body to GetTransactionSignWeightServlet.doPost
- Attacker controls: request/transaction/contract inputs to `GetTransactionSignWeightServlet.doPost` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends malformed body to GetTransactionSignWeightServlet.doPost that throws an unhandled exception path capable of exhausting threads or leaking a stack trace
- Invariant to test: malformed input yields a bounded 4xx, never an unhandled fatal or secret leak
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: send truncated/garbage payloads and assert graceful 400
