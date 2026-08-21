# Q3378: GetTransactionInfoByBlockNumServlet: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `GetTransactionInfoByBlockNumServlet.doPost` in `framework/src/main/java/org/tron/core/services/http/GetTransactionInfoByBlockNumServlet.java` — where the attacker sends an address/param to GetTransactionInfoByBlockNumServlet.doPost in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that GetTransactionInfoByBlockNumServlet.doPost and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/GetTransactionInfoByBlockNumServlet.java` -> `GetTransactionInfoByBlockNumServlet.doPost`
- Entrypoint: HTTP request to GetTransactionInfoByBlockNumServlet.doPost with dual-form address
- Attacker controls: request/transaction/contract inputs to `GetTransactionInfoByBlockNumServlet.doPost` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to GetTransactionInfoByBlockNumServlet.doPost in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: GetTransactionInfoByBlockNumServlet.doPost and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
