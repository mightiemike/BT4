# Q3488: PostParams: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `PostParams.getPostParams` in `framework/src/main/java/org/tron/core/services/http/PostParams.java` — where the attacker sends an address/param to PostParams.getPostParams in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that PostParams.getPostParams and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/PostParams.java` -> `PostParams.getPostParams`
- Entrypoint: HTTP request to PostParams.getPostParams with dual-form address
- Attacker controls: request/transaction/contract inputs to `PostParams.getPostParams` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to PostParams.getPostParams in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: PostParams.getPostParams and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
