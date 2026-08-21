# Q3177: ContractStore: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `ContractStore.getTotalContracts` in `chainbase/src/main/java/org/tron/core/store/ContractStore.java` — where the attacker inflates the revoking/undo set through operations touching ContractStore.getTotalContracts, growing memory per block — to break the invariant that undo state in ContractStore.getTotalContracts is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/ContractStore.java` -> `ContractStore.getTotalContracts`
- Entrypoint: many state writes via ContractStore.getTotalContracts
- Attacker controls: request/transaction/contract inputs to `ContractStore.getTotalContracts` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching ContractStore.getTotalContracts, growing memory per block
- Invariant to test: undo state in ContractStore.getTotalContracts is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
