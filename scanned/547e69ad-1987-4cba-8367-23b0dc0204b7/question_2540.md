# Q2540: GetPaginatedNowWitnessListServlet: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `GetPaginatedNowWitnessListServlet.doPost` in `framework/src/main/java/org/tron/core/services/http/GetPaginatedNowWitnessListServlet.java` — where the attacker sends an address/param to GetPaginatedNowWitnessListServlet.doPost in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that GetPaginatedNowWitnessListServlet.doPost and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/GetPaginatedNowWitnessListServlet.java` -> `GetPaginatedNowWitnessListServlet.doPost`
- Entrypoint: HTTP request to GetPaginatedNowWitnessListServlet.doPost with dual-form address
- Attacker controls: request/transaction/contract inputs to `GetPaginatedNowWitnessListServlet.doPost` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to GetPaginatedNowWitnessListServlet.doPost in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: GetPaginatedNowWitnessListServlet.doPost and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
