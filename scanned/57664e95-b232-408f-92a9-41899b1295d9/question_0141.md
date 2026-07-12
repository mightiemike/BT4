# Q141: LockTier owner binding against position delegator account

## Question
Can unprivileged owner account enter through `msgServer.LockTier` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, Id, Amount, ValidatorAddress, focusing on position primary store, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then submit MsgLockTier, force immediate exit, then claim rewards and attempt early exit cleanup, causing `position delegator account` to diverge so the invariant `TriggerExitImmediately cannot shorten economic lock rules below the tier exit duration` fails and the attacker can capture base rewards by corrupting the distribution withdraw address for the position delegator, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.LockTier
- Entrypoint: Cosmos SDK MsgLockTier transaction
- Attacker controls: Owner, Id, Amount, ValidatorAddress, TriggerExitImmediately, transaction ordering
- Exploit idea: capture base rewards by corrupting the distribution withdraw address for the position delegator by testing the owner binding angle against `position delegator account` during `submit MsgLockTier, force immediate exit, then claim rewards and attempt early exit cleanup`, with specific focus on position primary store.
- Invariant to test: TriggerExitImmediately cannot shorten economic lock rules below the tier exit duration; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test around LockTier plus bank, staking, distribution, and tieredrewards stores; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
