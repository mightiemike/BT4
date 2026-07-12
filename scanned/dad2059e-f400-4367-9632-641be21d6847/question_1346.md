# Q1346: ExitTierWithDelegation owner binding against owner delegation shares

## Question
Can position owner enter through `msgServer.ExitTierWithDelegation` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, validator exchange rate, focusing on bank balance deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then full exit then inspect deleted position indexes, withdraw routing, and dust sweep, causing `owner delegation shares` to diverge so the invariant `partial exit leaves remaining position value above tier MinLockAmount` fails and the attacker can use amount boundary equal to positionAmount to trigger wrong full/partial branch, leading to Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ExitTierWithDelegation
- Entrypoint: Cosmos SDK MsgExitTierWithDelegation transaction
- Attacker controls: Owner, PositionId, Amount, validator exchange rate, position shares
- Exploit idea: use amount boundary equal to positionAmount to trigger wrong full/partial branch by testing the owner binding angle against `owner delegation shares` during `full exit then inspect deleted position indexes, withdraw routing, and dust sweep`, with specific focus on bank balance deltas.
- Invariant to test: partial exit leaves remaining position value above tier MinLockAmount; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test comparing staking shares and token balances before and after ExitTierWithDelegation; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
