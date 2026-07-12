# Q90: LockTier owner binding against delegation shares

## Question
Can unprivileged owner account enter through `msgServer.LockTier` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, Id, Amount, ValidatorAddress, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then lock to a validator near a slashing or unbonding transition and then move through exit or redelegation paths, causing `delegation shares` to diverge so the invariant `created position owner, delegator address, tier id, validator, and distribution withdraw address remain bound to the signer` fails and the attacker can bypass minimum-lock or exit-duration economics and recover bonded funds before the tier permits it, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.LockTier
- Entrypoint: Cosmos SDK MsgLockTier transaction
- Attacker controls: Owner, Id, Amount, ValidatorAddress, TriggerExitImmediately, transaction ordering
- Exploit idea: bypass minimum-lock or exit-duration economics and recover bonded funds before the tier permits it by testing the owner binding angle against `delegation shares` during `lock to a validator near a slashing or unbonding transition and then move through exit or redelegation paths`, with specific focus on staking share deltas.
- Invariant to test: created position owner, delegator address, tier id, validator, and distribution withdraw address remain bound to the signer; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test around LockTier plus bank, staking, distribution, and tieredrewards stores; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
