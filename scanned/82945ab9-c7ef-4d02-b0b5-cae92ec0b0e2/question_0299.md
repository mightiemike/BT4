# Q299: TierUndelegate owner binding against position Delegation

## Question
Can position owner enter through `msgServer.TierUndelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, position exit state, focusing on bank balance deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then trigger exit, advance to the exact unlock boundary, then call TierUndelegate and WithdrawFromTier, causing `position Delegation` to diverge so the invariant `position state switches to undelegated without leaving stale validator count or reward checkpoints` fails and the attacker can claim the same rewards before and during undelegation by failing to advance checkpoints, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierUndelegate
- Entrypoint: Cosmos SDK MsgTierUndelegate transaction
- Attacker controls: Owner, PositionId, block time, position exit state, validator state
- Exploit idea: claim the same rewards before and during undelegation by failing to advance checkpoints by testing the owner binding angle against `position Delegation` during `trigger exit, advance to the exact unlock boundary, then call TierUndelegate and WithdrawFromTier`, with specific focus on bank balance deltas.
- Invariant to test: position state switches to undelegated without leaving stale validator count or reward checkpoints; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state-machine test over TriggerExitFromTier, TierUndelegate, staking unbonding, and WithdrawFromTier; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
