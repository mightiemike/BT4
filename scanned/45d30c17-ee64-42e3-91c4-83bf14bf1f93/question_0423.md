# Q423: TierUndelegate owner binding against LastBonusAccrual

## Question
Can position owner enter through `msgServer.TierUndelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, position exit state, focusing on position primary store, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then repeat ClearPosition and TierUndelegate around the exit lock boundary, causing `LastBonusAccrual` to diverge so the invariant `base and bonus rewards are claimed once before delegation shares leave the position` fails and the attacker can start unbonding another user's delegated position and later withdraw its unlocked funds, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierUndelegate
- Entrypoint: Cosmos SDK MsgTierUndelegate transaction
- Attacker controls: Owner, PositionId, block time, position exit state, validator state
- Exploit idea: start unbonding another user's delegated position and later withdraw its unlocked funds by testing the owner binding angle against `LastBonusAccrual` during `repeat ClearPosition and TierUndelegate around the exit lock boundary`, with specific focus on position primary store.
- Invariant to test: base and bonus rewards are claimed once before delegation shares leave the position; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state-machine test over TriggerExitFromTier, TierUndelegate, staking unbonding, and WithdrawFromTier; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
