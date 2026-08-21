# Q3420: GetDelegatedResourceAccountIndexServlet: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `GetDelegatedResourceAccountIndexServlet.doGet` in `framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexServlet.java` — where the attacker sends an address/param to GetDelegatedResourceAccountIndexServlet.doGet in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that GetDelegatedResourceAccountIndexServlet.doGet and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexServlet.java` -> `GetDelegatedResourceAccountIndexServlet.doGet`
- Entrypoint: HTTP request to GetDelegatedResourceAccountIndexServlet.doGet with dual-form address
- Attacker controls: request/transaction/contract inputs to `GetDelegatedResourceAccountIndexServlet.doGet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to GetDelegatedResourceAccountIndexServlet.doGet in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: GetDelegatedResourceAccountIndexServlet.doGet and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
