# Q2562: snapshot-rollback mismatch in LogInfo.getAddress

## Question
Can an unprivileged attacker trigger /wallet/triggerconstantcontract so common/src/main/java/org/tron/common/runtime/vm/LogInfo.java::getAddress merges one repository snapshot while discarding another, leaving TVM storage, balances, or repository state and receipts, refunds, internal transfers, or log state from different execution branches and causing Deterministic invalid state divergence or unauthorized partial commit?

## Target
- File/function: common/src/main/java/org/tron/common/runtime/vm/LogInfo.java::getAddress
- Entrypoint: /wallet/triggerconstantcontract
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Stress nested snapshots, child calls, create failures, and partial commits that cross repository or contract-state boundaries.
- Invariant to test: Every successful execution branch must atomically commit one coherent snapshot; failed branches must commit none of their state.
- Expected Immunefi impact: Deterministic invalid state divergence or unauthorized partial commit
- Fast validation: Drive nested execution trees via /wallet/triggerconstantcontract and compare repository branches before and after failures to detect split-brain commits.
