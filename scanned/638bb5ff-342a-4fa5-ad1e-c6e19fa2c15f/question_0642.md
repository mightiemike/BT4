# Q642: AddToTierPosition owner binding against LastBonusAccrual

## Question
Can position owner enter through `msgServer.AddToTierPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, position exit state, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then claim pending rewards, add a boundary amount, and immediately claim again, causing `LastBonusAccrual` to diverge so the invariant `new shares correspond only to the added amount at the current validator exchange rate` fails and the attacker can corrupt reward checkpoints so base or bonus rewards are paid twice, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.AddToTierPosition
- Entrypoint: Cosmos SDK MsgAddToTierPosition transaction
- Attacker controls: Owner, PositionId, Amount, position exit state, validator exchange rate
- Exploit idea: corrupt reward checkpoints so base or bonus rewards are paid twice by testing the owner binding angle against `LastBonusAccrual` during `claim pending rewards, add a boundary amount, and immediately claim again`, with specific focus on staking share deltas.
- Invariant to test: new shares correspond only to the added amount at the current validator exchange rate; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over AddToTierPosition, claimRewards, delegate, and position state conversion; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
