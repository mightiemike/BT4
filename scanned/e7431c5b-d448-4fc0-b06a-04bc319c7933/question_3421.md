# Q3421: DeployContractServlet: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `DeployContractServlet.doPost` in `framework/src/main/java/org/tron/core/services/http/DeployContractServlet.java` — where the attacker sends an address/param to DeployContractServlet.doPost in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that DeployContractServlet.doPost and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/DeployContractServlet.java` -> `DeployContractServlet.doPost`
- Entrypoint: HTTP request to DeployContractServlet.doPost with dual-form address
- Attacker controls: request/transaction/contract inputs to `DeployContractServlet.doPost` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to DeployContractServlet.doPost in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: DeployContractServlet.doPost and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
