# Q3821: EstimateEnergyServlet: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `EstimateEnergyServlet.doPost` in `framework/src/main/java/org/tron/core/services/http/EstimateEnergyServlet.java` — where the attacker sends an address/param to EstimateEnergyServlet.doPost in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that EstimateEnergyServlet.doPost and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/EstimateEnergyServlet.java` -> `EstimateEnergyServlet.doPost`
- Entrypoint: HTTP request to EstimateEnergyServlet.doPost with dual-form address
- Attacker controls: request/transaction/contract inputs to `EstimateEnergyServlet.doPost` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to EstimateEnergyServlet.doPost in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: EstimateEnergyServlet.doPost and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
