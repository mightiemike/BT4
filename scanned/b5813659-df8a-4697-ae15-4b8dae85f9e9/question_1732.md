# Q1732: transferDelegationFromPosition owner binding against owner shares

## Question
Can position owner enter through `Keeper.transferDelegationFromPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling PositionState, valAddr, amount, validator exchange rate, focusing on position primary store, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then repeat partial exits until just above min lock and then claim rewards, causing `owner shares` to diverge so the invariant `rounding cannot inflate ownerNewShares versus transferredAmount` fails and the attacker can recover more delegated stake than the position owns through share rounding, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/transfer_delegation.go::Keeper.transferDelegationFromPosition
- Entrypoint: MsgExitTierWithDelegation internal delegation transfer
- Attacker controls: PositionState, valAddr, amount, validator exchange rate, redelegation state
- Exploit idea: recover more delegated stake than the position owns through share rounding by testing the owner binding angle against `owner shares` during `repeat partial exits until just above min lock and then claim rewards`, with specific focus on position primary store.
- Invariant to test: rounding cannot inflate ownerNewShares versus transferredAmount; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper fuzz test over amount/share boundaries around ExitTierWithDelegation; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
