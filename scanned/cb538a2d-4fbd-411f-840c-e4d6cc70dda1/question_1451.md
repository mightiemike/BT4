# Q1451: ExitTierWithDelegation owner binding against min lock requirement

## Question
Can position owner enter through `msgServer.ExitTierWithDelegation` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, validator exchange rate, focusing on position primary store, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then full exit then inspect deleted position indexes, withdraw routing, and dust sweep, causing `min lock requirement` to diverge so the invariant `partial exit leaves remaining position value above tier MinLockAmount` fails and the attacker can full-exit while stale indexes or reward routing remain and claim again later, leading to Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ExitTierWithDelegation
- Entrypoint: Cosmos SDK MsgExitTierWithDelegation transaction
- Attacker controls: Owner, PositionId, Amount, validator exchange rate, position shares
- Exploit idea: full-exit while stale indexes or reward routing remain and claim again later by testing the owner binding angle against `min lock requirement` during `full exit then inspect deleted position indexes, withdraw routing, and dust sweep`, with specific focus on position primary store.
- Invariant to test: partial exit leaves remaining position value above tier MinLockAmount; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test comparing staking shares and token balances before and after ExitTierWithDelegation; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
