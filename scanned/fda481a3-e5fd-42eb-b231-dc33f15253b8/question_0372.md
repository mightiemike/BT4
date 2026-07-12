# Q372: TierUndelegate owner binding against position Delegation

## Question
Can position owner enter through `msgServer.TierUndelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, position exit state, focusing on distribution withdraw address routing, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then undelegate after the validator becomes unbonded or removed and then attempt withdrawal, causing `position Delegation` to diverge so the invariant `withdrawable unbonded funds remain in the position delegator until the owner-only withdrawal` fails and the attacker can claim the same rewards before and during undelegation by failing to advance checkpoints, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierUndelegate
- Entrypoint: Cosmos SDK MsgTierUndelegate transaction
- Attacker controls: Owner, PositionId, block time, position exit state, validator state
- Exploit idea: claim the same rewards before and during undelegation by failing to advance checkpoints by testing the owner binding angle against `position Delegation` during `undelegate after the validator becomes unbonded or removed and then attempt withdrawal`, with specific focus on distribution withdraw address routing.
- Invariant to test: withdrawable unbonded funds remain in the position delegator until the owner-only withdrawal; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state-machine test over TriggerExitFromTier, TierUndelegate, staking unbonding, and WithdrawFromTier; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
