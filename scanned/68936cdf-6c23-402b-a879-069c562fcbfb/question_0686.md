# Q686: AddToTierPosition owner binding against position amount

## Question
Can position owner enter through `msgServer.AddToTierPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, position exit state, focusing on distribution withdraw address routing, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then add funds while validator events are pending and compare rewards before and after setPosition, causing `position amount` to diverge so the invariant `new shares correspond only to the added amount at the current validator exchange rate` fails and the attacker can lock owner funds to an incorrect delegator account and later sweep them through withdrawal, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.AddToTierPosition
- Entrypoint: Cosmos SDK MsgAddToTierPosition transaction
- Attacker controls: Owner, PositionId, Amount, position exit state, validator exchange rate
- Exploit idea: lock owner funds to an incorrect delegator account and later sweep them through withdrawal by testing the owner binding angle against `position amount` during `add funds while validator events are pending and compare rewards before and after setPosition`, with specific focus on distribution withdraw address routing.
- Invariant to test: new shares correspond only to the added amount at the current validator exchange rate; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over AddToTierPosition, claimRewards, delegate, and position state conversion; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
