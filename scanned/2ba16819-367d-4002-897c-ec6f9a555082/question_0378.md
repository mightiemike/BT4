# Q378: TierUndelegate owner binding against ExitUnlockAt

## Question
Can position owner enter through `msgServer.TierUndelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, position exit state, focusing on distribution withdraw address routing, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then repeat ClearPosition and TierUndelegate around the exit lock boundary, causing `ExitUnlockAt` to diverge so the invariant `position state switches to undelegated without leaving stale validator count or reward checkpoints` fails and the attacker can start unbonding another user's delegated position and later withdraw its unlocked funds, leading to Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierUndelegate
- Entrypoint: Cosmos SDK MsgTierUndelegate transaction
- Attacker controls: Owner, PositionId, block time, position exit state, validator state
- Exploit idea: start unbonding another user's delegated position and later withdraw its unlocked funds by testing the owner binding angle against `ExitUnlockAt` during `repeat ClearPosition and TierUndelegate around the exit lock boundary`, with specific focus on distribution withdraw address routing.
- Invariant to test: position state switches to undelegated without leaving stale validator count or reward checkpoints; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state-machine test over TriggerExitFromTier, TierUndelegate, staking unbonding, and WithdrawFromTier; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
