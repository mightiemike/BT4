# Q741: FullNodeHttpApiService: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `FullNodeHttpApiService.addFilter` in `framework/src/main/java/org/tron/core/services/http/FullNodeHttpApiService.java` — where the attacker sends an address/param to FullNodeHttpApiService.addFilter in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that FullNodeHttpApiService.addFilter and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/FullNodeHttpApiService.java` -> `FullNodeHttpApiService.addFilter`
- Entrypoint: HTTP request to FullNodeHttpApiService.addFilter with dual-form address
- Attacker controls: request/transaction/contract inputs to `FullNodeHttpApiService.addFilter` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to FullNodeHttpApiService.addFilter in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: FullNodeHttpApiService.addFilter and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
