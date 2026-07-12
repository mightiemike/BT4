# Q734: AddToTierPosition owner binding against LastBonusAccrual

## Question
Can position owner enter through `msgServer.AddToTierPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, position exit state, focusing on owner/tier/validator indexes, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then add dust-sized or minimum-sized funds to a large position and exit partially, causing `LastBonusAccrual` to diverge so the invariant `new shares correspond only to the added amount at the current validator exchange rate` fails and the attacker can round added amount into excess delegation shares and later withdraw more than deposited, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.AddToTierPosition
- Entrypoint: Cosmos SDK MsgAddToTierPosition transaction
- Attacker controls: Owner, PositionId, Amount, position exit state, validator exchange rate
- Exploit idea: round added amount into excess delegation shares and later withdraw more than deposited by testing the owner binding angle against `LastBonusAccrual` during `add dust-sized or minimum-sized funds to a large position and exit partially`, with specific focus on owner/tier/validator indexes.
- Invariant to test: new shares correspond only to the added amount at the current validator exchange rate; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over AddToTierPosition, claimRewards, delegate, and position state conversion; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
