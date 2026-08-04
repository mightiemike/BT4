# Q1086: snapshot-rollback mismatch in VoteRewardUtil.adjustAllowance

## Question
Can an unprivileged attacker trigger /wallet/undelegateresource -> sign -> /wallet/broadcasttransaction so actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java::adjustAllowance merges one repository snapshot while discarding another, leaving frozen balances, delegated resources, or reward state and withdrawable amounts, vote weight, or receiver entitlements from different execution branches and causing Deterministic invalid state divergence or unauthorized partial commit?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java::adjustAllowance
- Entrypoint: /wallet/undelegateresource -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner/receiver addresses, resource type, amount, unfreeze or withdraw indexes, permission_id, and signatures
- Exploit idea: Stress nested snapshots, child calls, create failures, and partial commits that cross repository or contract-state boundaries.
- Invariant to test: Every successful execution branch must atomically commit one coherent snapshot; failed branches must commit none of their state.
- Expected Immunefi impact: Deterministic invalid state divergence or unauthorized partial commit
- Fast validation: Drive nested execution trees via /wallet/undelegateresource -> sign -> /wallet/broadcasttransaction and compare repository branches before and after failures to detect split-brain commits.
