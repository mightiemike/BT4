# Q3985: ContractStore: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `ContractStore.getTotalContracts` in `chainbase/src/main/java/org/tron/core/store/ContractStore.java` — where the attacker seeds keys so a query iterating ContractStore.getTotalContracts performs an unbounded prefix scan on each request — to break the invariant that iteration in ContractStore.getTotalContracts is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/ContractStore.java` -> `ContractStore.getTotalContracts`
- Entrypoint: query backed by ContractStore.getTotalContracts after seeding keys
- Attacker controls: request/transaction/contract inputs to `ContractStore.getTotalContracts` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating ContractStore.getTotalContracts performs an unbounded prefix scan on each request
- Invariant to test: iteration in ContractStore.getTotalContracts is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring ContractStore.getTotalContracts scan growth
