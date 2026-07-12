# Q1388: ExitTierWithDelegation owner binding against min lock requirement

## Question
Can position owner enter through `msgServer.ExitTierWithDelegation` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, validator exchange rate, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then exit with amount equal to positionAmount minus one rounding unit, causing `min lock requirement` to diverge so the invariant `partial exit leaves remaining position value above tier MinLockAmount` fails and the attacker can round shares so owner receives more delegated stake than removed from the position, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ExitTierWithDelegation
- Entrypoint: Cosmos SDK MsgExitTierWithDelegation transaction
- Attacker controls: Owner, PositionId, Amount, validator exchange rate, position shares
- Exploit idea: round shares so owner receives more delegated stake than removed from the position by testing the owner binding angle against `min lock requirement` during `exit with amount equal to positionAmount minus one rounding unit`, with specific focus on staking share deltas.
- Invariant to test: partial exit leaves remaining position value above tier MinLockAmount; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test comparing staking shares and token balances before and after ExitTierWithDelegation; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
