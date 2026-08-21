# Q1596: GetBlockByLimitNextServlet: param decode desync

## Question
Can an unprivileged attacker (HTTP servlet) abuse `GetBlockByLimitNextServlet.doPost` in `framework/src/main/java/org/tron/core/services/http/GetBlockByLimitNextServlet.java` — where the attacker sends a request whose doPost path parses a field differently than the actuator that later consumes it, so a value validated as benign is executed as hostile — to break the invariant that the value validated at the HTTP boundary equals the value executed by the actuator, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/GetBlockByLimitNextServlet.java` -> `GetBlockByLimitNextServlet.doPost`
- Entrypoint: HTTP POST to the servlet backing GetBlockByLimitNextServlet.doPost
- Attacker controls: request/transaction/contract inputs to `GetBlockByLimitNextServlet.doPost` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a request whose doPost path parses a field differently than the actuator that later consumes it, so a value validated as benign is executed as hostile
- Invariant to test: the value validated at the HTTP boundary equals the value executed by the actuator
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: controller test posting crafted JSON/hex and asserting parsed == executed field
