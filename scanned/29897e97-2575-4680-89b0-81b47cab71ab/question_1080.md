# Q1080: AssetIssueV2Store: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `AssetIssueV2Store.<primary method>` in `chainbase/src/main/java/org/tron/core/store/AssetIssueV2Store.java` — where the attacker inflates the revoking/undo set through operations touching AssetIssueV2Store.<primary method>, growing memory per block — to break the invariant that undo state in AssetIssueV2Store.<primary method> is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AssetIssueV2Store.java` -> `AssetIssueV2Store.<primary method>`
- Entrypoint: many state writes via AssetIssueV2Store.<primary method>
- Attacker controls: request/transaction/contract inputs to `AssetIssueV2Store.<primary method>` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching AssetIssueV2Store.<primary method>, growing memory per block
- Invariant to test: undo state in AssetIssueV2Store.<primary method> is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
