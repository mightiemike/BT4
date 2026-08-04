# Q2718: snapshot-rollback mismatch in VMConfig.initAllowMultiSign

## Question
Can an unprivileged attacker trigger /wallet/deploycontract -> sign -> /wallet/broadcasttransaction so common/src/main/java/org/tron/core/vm/config/VMConfig.java::initAllowMultiSign merges one repository snapshot while discarding another, leaving TVM storage, balances, or repository state and receipts, refunds, internal transfers, or log state from different execution branches and causing Deterministic invalid state divergence or unauthorized partial commit?

## Target
- File/function: common/src/main/java/org/tron/core/vm/config/VMConfig.java::initAllowMultiSign
- Entrypoint: /wallet/deploycontract -> sign -> /wallet/broadcasttransaction
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Stress nested snapshots, child calls, create failures, and partial commits that cross repository or contract-state boundaries.
- Invariant to test: Every successful execution branch must atomically commit one coherent snapshot; failed branches must commit none of their state.
- Expected Immunefi impact: Deterministic invalid state divergence or unauthorized partial commit
- Fast validation: Drive nested execution trees via /wallet/deploycontract -> sign -> /wallet/broadcasttransaction and compare repository branches before and after failures to detect split-brain commits.
