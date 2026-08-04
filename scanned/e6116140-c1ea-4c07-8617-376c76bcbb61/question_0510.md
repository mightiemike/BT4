# Q510: snapshot-rollback mismatch in ChainParameterEnum.fromCode

## Question
Can an unprivileged attacker trigger /wallet/broadcasthex so actuator/src/main/java/org/tron/core/vm/ChainParameterEnum.java::fromCode merges one repository snapshot while discarding another, leaving transaction-processing state and the resulting accounting, receipt, or index state from different execution branches and causing Deterministic invalid state divergence or unauthorized partial commit?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/ChainParameterEnum.java::fromCode
- Entrypoint: /wallet/broadcasthex
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Stress nested snapshots, child calls, create failures, and partial commits that cross repository or contract-state boundaries.
- Invariant to test: Every successful execution branch must atomically commit one coherent snapshot; failed branches must commit none of their state.
- Expected Immunefi impact: Deterministic invalid state divergence or unauthorized partial commit
- Fast validation: Drive nested execution trees via /wallet/broadcasthex and compare repository branches before and after failures to detect split-brain commits.
