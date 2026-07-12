# Q122: LockTier owner binding against PositionCountByValidator

## Question
Can unprivileged owner account enter through `msgServer.LockTier` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, Id, Amount, ValidatorAddress, focusing on distribution withdraw address routing, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then submit several MsgLockTier calls in adjacent blocks with different validators and boundary amounts, causing `PositionCountByValidator` to diverge so the invariant `position indexes and validator counts match the single active delegation created for the position` fails and the attacker can create a position whose indexes disagree with the actual delegation and let rewards or withdrawals route to the wrong owner, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.LockTier
- Entrypoint: Cosmos SDK MsgLockTier transaction
- Attacker controls: Owner, Id, Amount, ValidatorAddress, TriggerExitImmediately, transaction ordering
- Exploit idea: create a position whose indexes disagree with the actual delegation and let rewards or withdrawals route to the wrong owner by testing the owner binding angle against `PositionCountByValidator` during `submit several MsgLockTier calls in adjacent blocks with different validators and boundary amounts`, with specific focus on distribution withdraw address routing.
- Invariant to test: position indexes and validator counts match the single active delegation created for the position; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test around LockTier plus bank, staking, distribution, and tieredrewards stores; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
