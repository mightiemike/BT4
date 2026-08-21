# Q3335: TriggerSmartContractServlet: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `TriggerSmartContractServlet.validateParameter` in `framework/src/main/java/org/tron/core/services/http/TriggerSmartContractServlet.java` — where the attacker sends an address/param to TriggerSmartContractServlet.validateParameter in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that TriggerSmartContractServlet.validateParameter and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/TriggerSmartContractServlet.java` -> `TriggerSmartContractServlet.validateParameter`
- Entrypoint: HTTP request to TriggerSmartContractServlet.validateParameter with dual-form address
- Attacker controls: request/transaction/contract inputs to `TriggerSmartContractServlet.validateParameter` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to TriggerSmartContractServlet.validateParameter in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: TriggerSmartContractServlet.validateParameter and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
