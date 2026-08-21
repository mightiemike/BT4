# Q2054: ContractStore: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `ContractStore.findContractByHash` in `chainbase/src/main/java/org/tron/core/store/ContractStore.java` — where the attacker inflates the revoking/undo set through operations touching ContractStore.findContractByHash, growing memory per block — to break the invariant that undo state in ContractStore.findContractByHash is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/ContractStore.java` -> `ContractStore.findContractByHash`
- Entrypoint: many state writes via ContractStore.findContractByHash
- Attacker controls: request/transaction/contract inputs to `ContractStore.findContractByHash` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching ContractStore.findContractByHash, growing memory per block
- Invariant to test: undo state in ContractStore.findContractByHash is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
