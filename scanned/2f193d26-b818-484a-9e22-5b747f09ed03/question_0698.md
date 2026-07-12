# Q698: AddToTierPosition owner binding against delegation shares

## Question
Can position owner enter through `msgServer.AddToTierPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, position exit state, focusing on position primary store, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then add to a position right after a validator slash changes token-per-share, causing `delegation shares` to diverge so the invariant `owner funds move into the position delegator exactly once before delegation` fails and the attacker can lock owner funds to an incorrect delegator account and later sweep them through withdrawal, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.AddToTierPosition
- Entrypoint: Cosmos SDK MsgAddToTierPosition transaction
- Attacker controls: Owner, PositionId, Amount, position exit state, validator exchange rate
- Exploit idea: lock owner funds to an incorrect delegator account and later sweep them through withdrawal by testing the owner binding angle against `delegation shares` during `add to a position right after a validator slash changes token-per-share`, with specific focus on position primary store.
- Invariant to test: owner funds move into the position delegator exactly once before delegation; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over AddToTierPosition, claimRewards, delegate, and position state conversion; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
