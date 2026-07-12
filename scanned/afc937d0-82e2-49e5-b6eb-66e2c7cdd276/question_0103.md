# Q103: LockTier owner binding against NextPositionId

## Question
Can unprivileged owner account enter through `msgServer.LockTier` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, Id, Amount, ValidatorAddress, focusing on distribution withdraw address routing, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then lock exactly tier minimum amount, then trigger validator status and reward checkpoint changes before claim, causing `NextPositionId` to diverge so the invariant `TriggerExitImmediately cannot shorten economic lock rules below the tier exit duration` fails and the attacker can derive or reuse an unintended delegator account so later withdrawals sweep another user's funds, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.LockTier
- Entrypoint: Cosmos SDK MsgLockTier transaction
- Attacker controls: Owner, Id, Amount, ValidatorAddress, TriggerExitImmediately, transaction ordering
- Exploit idea: derive or reuse an unintended delegator account so later withdrawals sweep another user's funds by testing the owner binding angle against `NextPositionId` during `lock exactly tier minimum amount, then trigger validator status and reward checkpoint changes before claim`, with specific focus on distribution withdraw address routing.
- Invariant to test: TriggerExitImmediately cannot shorten economic lock rules below the tier exit duration; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test around LockTier plus bank, staking, distribution, and tieredrewards stores; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
