# Q736: AddToTierPosition owner binding against LastBonusAccrual

## Question
Can position owner enter through `msgServer.AddToTierPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, position exit state, focusing on owner/tier/validator indexes, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then add funds while validator events are pending and compare rewards before and after setPosition, causing `LastBonusAccrual` to diverge so the invariant `positions in triggered exit cannot receive more locked funds` fails and the attacker can make added stake accrue rewards for time before it was locked, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.AddToTierPosition
- Entrypoint: Cosmos SDK MsgAddToTierPosition transaction
- Attacker controls: Owner, PositionId, Amount, position exit state, validator exchange rate
- Exploit idea: make added stake accrue rewards for time before it was locked by testing the owner binding angle against `LastBonusAccrual` during `add funds while validator events are pending and compare rewards before and after setPosition`, with specific focus on owner/tier/validator indexes.
- Invariant to test: positions in triggered exit cannot receive more locked funds; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over AddToTierPosition, claimRewards, delegate, and position state conversion; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
