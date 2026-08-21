# Q1495: FullNodeHttpApiService: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `FullNodeHttpApiService.addServlet` in `framework/src/main/java/org/tron/core/services/http/FullNodeHttpApiService.java` — where the attacker sends an address/param to FullNodeHttpApiService.addServlet in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that FullNodeHttpApiService.addServlet and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/FullNodeHttpApiService.java` -> `FullNodeHttpApiService.addServlet`
- Entrypoint: HTTP request to FullNodeHttpApiService.addServlet with dual-form address
- Attacker controls: request/transaction/contract inputs to `FullNodeHttpApiService.addServlet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to FullNodeHttpApiService.addServlet in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: FullNodeHttpApiService.addServlet and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
