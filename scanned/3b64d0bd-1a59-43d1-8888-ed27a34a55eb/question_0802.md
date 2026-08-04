# Q802: cross-path inconsistency in DelegateResourceParam.getOwnerAddress

## Question
Can an unprivileged attacker reach the same logical stake, unfreeze, delegate, vote, or reward flow through two public paths, one via /wallet/delegateresource -> sign -> /wallet/broadcasttransaction and one via another supported build/broadcast route, so actuator/src/main/java/org/tron/core/vm/nativecontract/param/DelegateResourceParam.java::getOwnerAddress enforces different checks and the weaker path leads to Unauthorized unlocking, delegation, or withdrawal of staked TRX/resources?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/nativecontract/param/DelegateResourceParam.java::getOwnerAddress
- Entrypoint: /wallet/delegateresource -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner/receiver addresses, resource type, amount, unfreeze or withdraw indexes, permission_id, and signatures
- Exploit idea: Compare HTTP, gRPC, JSON-RPC, actuator, and native-contract entrypoints for the same state change and look for one path skipping a guard or normalizing input differently.
- Invariant to test: All public paths for the same logical stake, unfreeze, delegate, vote, or reward flow must enforce the same authorization, accounting, and one-time-settlement rules over frozen balances, delegated resources, or reward state/withdrawable amounts, vote weight, or receiver entitlements.
- Expected Immunefi impact: Unauthorized unlocking, delegation, or withdrawal of staked TRX/resources
- Fast validation: Replay the same logical action across all exposed APIs and assert they choose the same owner, charge the same fees, and apply identical accept/reject rules.
