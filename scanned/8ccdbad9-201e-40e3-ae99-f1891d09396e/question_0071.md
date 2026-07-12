# Q71: LockTier owner binding against PositionsByTier

## Question
Can unprivileged owner account enter through `msgServer.LockTier` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, Id, Amount, ValidatorAddress, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then submit MsgLockTier, force immediate exit, then claim rewards and attempt early exit cleanup, causing `PositionsByTier` to diverge so the invariant `locked bond denom moved from owner to the derived delegator account exactly once` fails and the attacker can create a position whose indexes disagree with the actual delegation and let rewards or withdrawals route to the wrong owner, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.LockTier
- Entrypoint: Cosmos SDK MsgLockTier transaction
- Attacker controls: Owner, Id, Amount, ValidatorAddress, TriggerExitImmediately, transaction ordering
- Exploit idea: create a position whose indexes disagree with the actual delegation and let rewards or withdrawals route to the wrong owner by testing the owner binding angle against `PositionsByTier` during `submit MsgLockTier, force immediate exit, then claim rewards and attempt early exit cleanup`, with specific focus on staking share deltas.
- Invariant to test: locked bond denom moved from owner to the derived delegator account exactly once; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test around LockTier plus bank, staking, distribution, and tieredrewards stores; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
