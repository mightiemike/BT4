# Q8: LockTier owner binding against position delegator account

## Question
Can unprivileged owner account enter through `msgServer.LockTier` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, Id, Amount, ValidatorAddress, focusing on bank balance deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then lock exactly tier minimum amount, then trigger validator status and reward checkpoint changes before claim, causing `position delegator account` to diverge so the invariant `position indexes and validator counts match the single active delegation created for the position` fails and the attacker can bypass minimum-lock or exit-duration economics and recover bonded funds before the tier permits it, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.LockTier
- Entrypoint: Cosmos SDK MsgLockTier transaction
- Attacker controls: Owner, Id, Amount, ValidatorAddress, TriggerExitImmediately, transaction ordering
- Exploit idea: bypass minimum-lock or exit-duration economics and recover bonded funds before the tier permits it by testing the owner binding angle against `position delegator account` during `lock exactly tier minimum amount, then trigger validator status and reward checkpoint changes before claim`, with specific focus on bank balance deltas.
- Invariant to test: position indexes and validator counts match the single active delegation created for the position; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test around LockTier plus bank, staking, distribution, and tieredrewards stores; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
