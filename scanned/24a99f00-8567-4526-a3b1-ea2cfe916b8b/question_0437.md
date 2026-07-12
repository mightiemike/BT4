# Q437: TierUndelegate owner binding against reward pool balance

## Question
Can position owner enter through `msgServer.TierUndelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, position exit state, focusing on position primary store, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then undelegate after the validator becomes unbonded or removed and then attempt withdrawal, causing `reward pool balance` to diverge so the invariant `withdrawable unbonded funds remain in the position delegator until the owner-only withdrawal` fails and the attacker can claim the same rewards before and during undelegation by failing to advance checkpoints, leading to Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierUndelegate
- Entrypoint: Cosmos SDK MsgTierUndelegate transaction
- Attacker controls: Owner, PositionId, block time, position exit state, validator state
- Exploit idea: claim the same rewards before and during undelegation by failing to advance checkpoints by testing the owner binding angle against `reward pool balance` during `undelegate after the validator becomes unbonded or removed and then attempt withdrawal`, with specific focus on position primary store.
- Invariant to test: withdrawable unbonded funds remain in the position delegator until the owner-only withdrawal; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state-machine test over TriggerExitFromTier, TierUndelegate, staking unbonding, and WithdrawFromTier; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
