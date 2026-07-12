# Q47: LockTier owner binding against owner balance

## Question
Can unprivileged owner account enter through `msgServer.LockTier` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, Id, Amount, ValidatorAddress, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then submit several MsgLockTier calls in adjacent blocks with different validators and boundary amounts, causing `owner balance` to diverge so the invariant `reward checkpoints begin at the validator's latest event sequence and cannot include pre-position rewards` fails and the attacker can make position creation observe stale validator events and over-accrue bonus rewards from before the lock, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.LockTier
- Entrypoint: Cosmos SDK MsgLockTier transaction
- Attacker controls: Owner, Id, Amount, ValidatorAddress, TriggerExitImmediately, transaction ordering
- Exploit idea: make position creation observe stale validator events and over-accrue bonus rewards from before the lock by testing the owner binding angle against `owner balance` during `submit several MsgLockTier calls in adjacent blocks with different validators and boundary amounts`, with specific focus on staking share deltas.
- Invariant to test: reward checkpoints begin at the validator's latest event sequence and cannot include pre-position rewards; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test around LockTier plus bank, staking, distribution, and tieredrewards stores; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
