# Q1363: ExitTierWithDelegation owner binding against position indexes

## Question
Can position owner enter through `msgServer.ExitTierWithDelegation` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, validator exchange rate, focusing on bank balance deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then exit with amount equal to positionAmount minus one rounding unit, causing `position indexes` to diverge so the invariant `partial exit leaves remaining position value above tier MinLockAmount` fails and the attacker can use amount boundary equal to positionAmount to trigger wrong full/partial branch, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ExitTierWithDelegation
- Entrypoint: Cosmos SDK MsgExitTierWithDelegation transaction
- Attacker controls: Owner, PositionId, Amount, validator exchange rate, position shares
- Exploit idea: use amount boundary equal to positionAmount to trigger wrong full/partial branch by testing the owner binding angle against `position indexes` during `exit with amount equal to positionAmount minus one rounding unit`, with specific focus on bank balance deltas.
- Invariant to test: partial exit leaves remaining position value above tier MinLockAmount; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test comparing staking shares and token balances before and after ExitTierWithDelegation; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
