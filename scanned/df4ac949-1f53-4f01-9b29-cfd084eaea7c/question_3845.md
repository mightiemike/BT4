# Q3845: GetDelegatedResourceAccountIndexV2Servlet: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `GetDelegatedResourceAccountIndexV2Servlet.doGet` in `framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java` — where the attacker sends an address/param to GetDelegatedResourceAccountIndexV2Servlet.doGet in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that GetDelegatedResourceAccountIndexV2Servlet.doGet and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java` -> `GetDelegatedResourceAccountIndexV2Servlet.doGet`
- Entrypoint: HTTP request to GetDelegatedResourceAccountIndexV2Servlet.doGet with dual-form address
- Attacker controls: request/transaction/contract inputs to `GetDelegatedResourceAccountIndexV2Servlet.doGet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to GetDelegatedResourceAccountIndexV2Servlet.doGet in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: GetDelegatedResourceAccountIndexV2Servlet.doGet and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
