# Q356: JsonFormat: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `JsonFormat.handleMissingField` in `framework/src/main/java/org/tron/core/services/http/JsonFormat.java` — where the attacker sends an address/param to JsonFormat.handleMissingField in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that JsonFormat.handleMissingField and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/JsonFormat.java` -> `JsonFormat.handleMissingField`
- Entrypoint: HTTP request to JsonFormat.handleMissingField with dual-form address
- Attacker controls: request/transaction/contract inputs to `JsonFormat.handleMissingField` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to JsonFormat.handleMissingField in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: JsonFormat.handleMissingField and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
