# Q1512: transferDelegationToPosition owner binding against source shares

## Question
Can delegator using MsgCommitDelegationToTier enter through `Keeper.transferDelegationToPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling owner, posDelAddr, validatorAddr, amount, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then commit just after validator token/share exchange rate changes, causing `source shares` to diverge so the invariant `source unbonded amount equals the amount redelegated to the position` fails and the attacker can split a delegation across positions to bypass min-lock or per-position accounting, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/transfer_delegation.go::Keeper.transferDelegationToPosition
- Entrypoint: MsgCommitDelegationToTier internal delegation transfer
- Attacker controls: owner, posDelAddr, validatorAddr, amount, incoming redelegation state
- Exploit idea: split a delegation across positions to bypass min-lock or per-position accounting by testing the owner binding angle against `source shares` during `commit just after validator token/share exchange rate changes`, with specific focus on staking share deltas.
- Invariant to test: source unbonded amount equals the amount redelegated to the position; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: staking keeper test around HasReceivingRedelegation, ValidateUnbondAmount, Unbond, and Delegate; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
