# Q1098: snapshot-rollback mismatch in CallCreate.getData

## Question
Can an unprivileged attacker trigger /wallet/estimateenergy so chainbase/src/main/java/org/tron/common/runtime/CallCreate.java::getData merges one repository snapshot while discarding another, leaving TVM storage, balances, or repository state and receipts, refunds, internal transfers, or log state from different execution branches and causing Deterministic invalid state divergence or unauthorized partial commit?

## Target
- File/function: chainbase/src/main/java/org/tron/common/runtime/CallCreate.java::getData
- Entrypoint: /wallet/estimateenergy
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Stress nested snapshots, child calls, create failures, and partial commits that cross repository or contract-state boundaries.
- Invariant to test: Every successful execution branch must atomically commit one coherent snapshot; failed branches must commit none of their state.
- Expected Immunefi impact: Deterministic invalid state divergence or unauthorized partial commit
- Fast validation: Drive nested execution trees via /wallet/estimateenergy and compare repository branches before and after failures to detect split-brain commits.
