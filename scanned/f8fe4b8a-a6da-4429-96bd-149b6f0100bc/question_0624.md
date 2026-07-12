# Q624: AddToTierPosition owner binding against position amount

## Question
Can position owner enter through `msgServer.AddToTierPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, position exit state, focusing on bank balance deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then add dust-sized or minimum-sized funds to a large position and exit partially, causing `position amount` to diverge so the invariant `new shares correspond only to the added amount at the current validator exchange rate` fails and the attacker can add funds after exit trigger to alter withdrawal eligibility or bypass exit economics, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.AddToTierPosition
- Entrypoint: Cosmos SDK MsgAddToTierPosition transaction
- Attacker controls: Owner, PositionId, Amount, position exit state, validator exchange rate
- Exploit idea: add funds after exit trigger to alter withdrawal eligibility or bypass exit economics by testing the owner binding angle against `position amount` during `add dust-sized or minimum-sized funds to a large position and exit partially`, with specific focus on bank balance deltas.
- Invariant to test: new shares correspond only to the added amount at the current validator exchange rate; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over AddToTierPosition, claimRewards, delegate, and position state conversion; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
