# Q2468: TriggerSmartContractServlet: uncaught exception 500-loop

## Question
Can an unprivileged attacker (HTTP servlet) abuse `TriggerSmartContractServlet.validateParameter` in `framework/src/main/java/org/tron/core/services/http/TriggerSmartContractServlet.java` — where the attacker sends malformed body to TriggerSmartContractServlet.validateParameter that throws an unhandled exception path capable of exhausting threads or leaking a stack trace — to break the invariant that malformed input yields a bounded 4xx, never an unhandled fatal or secret leak, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/TriggerSmartContractServlet.java` -> `TriggerSmartContractServlet.validateParameter`
- Entrypoint: malformed HTTP body to TriggerSmartContractServlet.validateParameter
- Attacker controls: request/transaction/contract inputs to `TriggerSmartContractServlet.validateParameter` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends malformed body to TriggerSmartContractServlet.validateParameter that throws an unhandled exception path capable of exhausting threads or leaking a stack trace
- Invariant to test: malformed input yields a bounded 4xx, never an unhandled fatal or secret leak
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: send truncated/garbage payloads and assert graceful 400
