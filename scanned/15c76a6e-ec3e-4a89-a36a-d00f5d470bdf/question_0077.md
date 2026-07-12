# Q77: LockTier owner binding against PositionCountByValidator

## Question
Can unprivileged owner account enter through `msgServer.LockTier` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, Id, Amount, ValidatorAddress, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then submit several MsgLockTier calls in adjacent blocks with different validators and boundary amounts, causing `PositionCountByValidator` to diverge so the invariant `locked bond denom moved from owner to the derived delegator account exactly once` fails and the attacker can make position creation observe stale validator events and over-accrue bonus rewards from before the lock, leading to Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.LockTier
- Entrypoint: Cosmos SDK MsgLockTier transaction
- Attacker controls: Owner, Id, Amount, ValidatorAddress, TriggerExitImmediately, transaction ordering
- Exploit idea: make position creation observe stale validator events and over-accrue bonus rewards from before the lock by testing the owner binding angle against `PositionCountByValidator` during `submit several MsgLockTier calls in adjacent blocks with different validators and boundary amounts`, with specific focus on staking share deltas.
- Invariant to test: locked bond denom moved from owner to the derived delegator account exactly once; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test around LockTier plus bank, staking, distribution, and tieredrewards stores; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
