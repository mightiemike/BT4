# Q117: LockTier owner binding against PositionsByTier

## Question
Can unprivileged owner account enter through `msgServer.LockTier` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, Id, Amount, ValidatorAddress, focusing on distribution withdraw address routing, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then submit several MsgLockTier calls in adjacent blocks with different validators and boundary amounts, causing `PositionsByTier` to diverge so the invariant `TriggerExitImmediately cannot shorten economic lock rules below the tier exit duration` fails and the attacker can create a position whose indexes disagree with the actual delegation and let rewards or withdrawals route to the wrong owner, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.LockTier
- Entrypoint: Cosmos SDK MsgLockTier transaction
- Attacker controls: Owner, Id, Amount, ValidatorAddress, TriggerExitImmediately, transaction ordering
- Exploit idea: create a position whose indexes disagree with the actual delegation and let rewards or withdrawals route to the wrong owner by testing the owner binding angle against `PositionsByTier` during `submit several MsgLockTier calls in adjacent blocks with different validators and boundary amounts`, with specific focus on distribution withdraw address routing.
- Invariant to test: TriggerExitImmediately cannot shorten economic lock rules below the tier exit duration; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test around LockTier plus bank, staking, distribution, and tieredrewards stores; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
