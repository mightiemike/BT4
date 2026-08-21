# Q410: Util: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `Util.printBlockToJSON` in `framework/src/main/java/org/tron/core/services/http/Util.java` — where the attacker sends an address/param to Util.printBlockToJSON in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that Util.printBlockToJSON and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/Util.java` -> `Util.printBlockToJSON`
- Entrypoint: HTTP request to Util.printBlockToJSON with dual-form address
- Attacker controls: request/transaction/contract inputs to `Util.printBlockToJSON` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to Util.printBlockToJSON in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: Util.printBlockToJSON and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
