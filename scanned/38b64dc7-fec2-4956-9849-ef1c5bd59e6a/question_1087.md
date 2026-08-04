# Q1087: call-depth cleanup bug in VoteRewardUtil.adjustAllowance

## Question
Can an unprivileged attacker use /wallet/unfreezebalancev2 -> sign -> /wallet/broadcasttransaction to push call depth, recursion, or nested create/call structure into a path where actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java::adjustAllowance forgets to clean up frozen balances, delegated resources, or reward state or withdrawable amounts, vote weight, or receiver entitlements, leading to Permanent contract or user-fund lock from broken cleanup?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java::adjustAllowance
- Entrypoint: /wallet/unfreezebalancev2 -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner/receiver addresses, resource type, amount, unfreeze or withdraw indexes, permission_id, and signatures
- Exploit idea: Create deeply nested or mutually recursive calls that hit limits only after temporary state and accounting structures are populated.
- Invariant to test: Depth limits and nested-frame exits must leave no surviving garbage or authorization/accounting residue in frozen balances, delegated resources, or reward state/withdrawable amounts, vote weight, or receiver entitlements.
- Expected Immunefi impact: Permanent contract or user-fund lock from broken cleanup
- Fast validation: Deploy contracts that hit depth and recursion edges via /wallet/unfreezebalancev2 -> sign -> /wallet/broadcasttransaction, then assert no stale storage, call-context, or balance artifacts survive.
