# Q637: JsonFormat: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `JsonFormat.handlePrimitive` in `framework/src/main/java/org/tron/core/services/http/JsonFormat.java` — where the attacker sends an address/param to JsonFormat.handlePrimitive in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that JsonFormat.handlePrimitive and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/JsonFormat.java` -> `JsonFormat.handlePrimitive`
- Entrypoint: HTTP request to JsonFormat.handlePrimitive with dual-form address
- Attacker controls: request/transaction/contract inputs to `JsonFormat.handlePrimitive` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to JsonFormat.handlePrimitive in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: JsonFormat.handlePrimitive and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
