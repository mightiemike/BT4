# Q1591: transferDelegationToPosition owner binding against position shares

## Question
Can delegator using MsgCommitDelegationToTier enter through `Keeper.transferDelegationToPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling owner, posDelAddr, validatorAddr, amount, focusing on owner/tier/validator indexes, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then commit a near-zero amount that validates but unbonds to zero newAmount, causing `position shares` to diverge so the invariant `position delegator address cannot equal the source delegator` fails and the attacker can transfer into a validator state that later cannot be exited without loss, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/transfer_delegation.go::Keeper.transferDelegationToPosition
- Entrypoint: MsgCommitDelegationToTier internal delegation transfer
- Attacker controls: owner, posDelAddr, validatorAddr, amount, incoming redelegation state
- Exploit idea: transfer into a validator state that later cannot be exited without loss by testing the owner binding angle against `position shares` during `commit a near-zero amount that validates but unbonds to zero newAmount`, with specific focus on owner/tier/validator indexes.
- Invariant to test: position delegator address cannot equal the source delegator; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: staking keeper test around HasReceivingRedelegation, ValidateUnbondAmount, Unbond, and Delegate; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
