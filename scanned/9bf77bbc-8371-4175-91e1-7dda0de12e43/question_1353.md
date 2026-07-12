# Q1353: ExitTierWithDelegation owner binding against remaining position amount

## Question
Can position owner enter through `msgServer.ExitTierWithDelegation` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, validator exchange rate, focusing on bank balance deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then exit with amount equal to positionAmount minus one rounding unit, causing `remaining position amount` to diverge so the invariant `transferred owner shares correspond exactly to unbonded position shares and transferred amount` fails and the attacker can use amount boundary equal to positionAmount to trigger wrong full/partial branch, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ExitTierWithDelegation
- Entrypoint: Cosmos SDK MsgExitTierWithDelegation transaction
- Attacker controls: Owner, PositionId, Amount, validator exchange rate, position shares
- Exploit idea: use amount boundary equal to positionAmount to trigger wrong full/partial branch by testing the owner binding angle against `remaining position amount` during `exit with amount equal to positionAmount minus one rounding unit`, with specific focus on bank balance deltas.
- Invariant to test: transferred owner shares correspond exactly to unbonded position shares and transferred amount; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test comparing staking shares and token balances before and after ExitTierWithDelegation; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
