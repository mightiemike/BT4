# Q368: TierUndelegate owner binding against reward pool balance

## Question
Can position owner enter through `msgServer.TierUndelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, position exit state, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then repeat ClearPosition and TierUndelegate around the exit lock boundary, causing `reward pool balance` to diverge so the invariant `position state switches to undelegated without leaving stale validator count or reward checkpoints` fails and the attacker can bypass the exit lock at exact timestamp boundaries and withdraw stake early, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierUndelegate
- Entrypoint: Cosmos SDK MsgTierUndelegate transaction
- Attacker controls: Owner, PositionId, block time, position exit state, validator state
- Exploit idea: bypass the exit lock at exact timestamp boundaries and withdraw stake early by testing the owner binding angle against `reward pool balance` during `repeat ClearPosition and TierUndelegate around the exit lock boundary`, with specific focus on staking share deltas.
- Invariant to test: position state switches to undelegated without leaving stale validator count or reward checkpoints; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state-machine test over TriggerExitFromTier, TierUndelegate, staking unbonding, and WithdrawFromTier; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
