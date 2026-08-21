# Q2290: GetAssetIssueListServlet: visible-1 boolean coercion

## Question
Can an unprivileged attacker (HTTP servlet) abuse `GetAssetIssueListServlet.doPost` in `framework/src/main/java/org/tron/core/services/http/GetAssetIssueListServlet.java` — where the attacker toggles the visible/permissionId/other flag consumed by GetAssetIssueListServlet.doPost to reinterpret address bytes vs base58, causing wrong owner resolution — to break the invariant that address interpretation is unambiguous regardless of client-set flags, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/GetAssetIssueListServlet.java` -> `GetAssetIssueListServlet.doPost`
- Entrypoint: HTTP request to GetAssetIssueListServlet.doPost with mismatched visible flag
- Attacker controls: request/transaction/contract inputs to `GetAssetIssueListServlet.doPost` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: toggles the visible/permissionId/other flag consumed by GetAssetIssueListServlet.doPost to reinterpret address bytes vs base58, causing wrong owner resolution
- Invariant to test: address interpretation is unambiguous regardless of client-set flags
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test with visible=true/false on same address bytes
