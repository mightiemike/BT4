# Q1879: GetAssetIssueListByNameServlet: numeric field overflow

## Question
Can an unprivileged attacker (HTTP servlet) abuse `GetAssetIssueListByNameServlet.doGet` in `framework/src/main/java/org/tron/core/services/http/GetAssetIssueListByNameServlet.java` — where the attacker passes a boundary/negative/oversized numeric field to GetAssetIssueListByNameServlet.doGet that overflows or wraps when converted to long/int before validation — to break the invariant that numeric params are range-checked before use in accounting or allocation, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/GetAssetIssueListByNameServlet.java` -> `GetAssetIssueListByNameServlet.doGet`
- Entrypoint: HTTP request with crafted numeric field to GetAssetIssueListByNameServlet.doGet
- Attacker controls: request/transaction/contract inputs to `GetAssetIssueListByNameServlet.doGet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes a boundary/negative/oversized numeric field to GetAssetIssueListByNameServlet.doGet that overflows or wraps when converted to long/int before validation
- Invariant to test: numeric params are range-checked before use in accounting or allocation
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: fuzz the numeric field across MIN/MAX/negative and assert rejection
