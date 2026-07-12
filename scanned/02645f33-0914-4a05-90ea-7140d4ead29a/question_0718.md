# Q718: AddToTierPosition owner binding against owner balance

## Question
Can position owner enter through `msgServer.AddToTierPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, position exit state, focusing on owner/tier/validator indexes, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then add to a position right after a validator slash changes token-per-share, causing `owner balance` to diverge so the invariant `pending rewards are claimed and checkpoints advanced before added stake accrues bonus` fails and the attacker can make added stake accrue rewards for time before it was locked, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.AddToTierPosition
- Entrypoint: Cosmos SDK MsgAddToTierPosition transaction
- Attacker controls: Owner, PositionId, Amount, position exit state, validator exchange rate
- Exploit idea: make added stake accrue rewards for time before it was locked by testing the owner binding angle against `owner balance` during `add to a position right after a validator slash changes token-per-share`, with specific focus on owner/tier/validator indexes.
- Invariant to test: pending rewards are claimed and checkpoints advanced before added stake accrues bonus; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over AddToTierPosition, claimRewards, delegate, and position state conversion; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
