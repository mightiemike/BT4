# Q3950: FullNodeHttpApiService: visible-1 boolean coercion

## Question
Can an unprivileged attacker (HTTP servlet) abuse `FullNodeHttpApiService.addServlet` in `framework/src/main/java/org/tron/core/services/http/FullNodeHttpApiService.java` — where the attacker toggles the visible/permissionId/other flag consumed by FullNodeHttpApiService.addServlet to reinterpret address bytes vs base58, causing wrong owner resolution — to break the invariant that address interpretation is unambiguous regardless of client-set flags, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/FullNodeHttpApiService.java` -> `FullNodeHttpApiService.addServlet`
- Entrypoint: HTTP request to FullNodeHttpApiService.addServlet with mismatched visible flag
- Attacker controls: request/transaction/contract inputs to `FullNodeHttpApiService.addServlet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: toggles the visible/permissionId/other flag consumed by FullNodeHttpApiService.addServlet to reinterpret address bytes vs base58, causing wrong owner resolution
- Invariant to test: address interpretation is unambiguous regardless of client-set flags
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test with visible=true/false on same address bytes
