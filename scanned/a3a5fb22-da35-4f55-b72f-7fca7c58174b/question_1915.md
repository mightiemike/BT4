# Q1915: ContractStore: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `ContractStore.getTotalContracts` in `chainbase/src/main/java/org/tron/core/store/ContractStore.java` — where the attacker triggers ContractStore.getTotalContracts paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in ContractStore.getTotalContracts is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/ContractStore.java` -> `ContractStore.getTotalContracts`
- Entrypoint: repeated queries via ContractStore.getTotalContracts
- Attacker controls: request/transaction/contract inputs to `ContractStore.getTotalContracts` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers ContractStore.getTotalContracts paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in ContractStore.getTotalContracts is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress ContractStore.getTotalContracts and watch handle/heap growth
