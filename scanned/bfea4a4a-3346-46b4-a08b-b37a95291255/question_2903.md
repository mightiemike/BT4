# Q2903: GetTransactionListFromPendingServlet: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `GetTransactionListFromPendingServlet.doGet` in `framework/src/main/java/org/tron/core/services/http/GetTransactionListFromPendingServlet.java` — where the attacker sends an address/param to GetTransactionListFromPendingServlet.doGet in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that GetTransactionListFromPendingServlet.doGet and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/GetTransactionListFromPendingServlet.java` -> `GetTransactionListFromPendingServlet.doGet`
- Entrypoint: HTTP request to GetTransactionListFromPendingServlet.doGet with dual-form address
- Attacker controls: request/transaction/contract inputs to `GetTransactionListFromPendingServlet.doGet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to GetTransactionListFromPendingServlet.doGet in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: GetTransactionListFromPendingServlet.doGet and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
