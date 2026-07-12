# Q998: ClearPosition owner binding against LastKnownBonded

## Question
Can position owner enter through `msgServer.ClearPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, unbonding state, focusing on position primary store, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then trigger exit, cross the unlock boundary, clear position, then add or redelegate, causing `LastKnownBonded` to diverge so the invariant `clearing exit claims rewards once and advances checkpoints correctly` fails and the attacker can clear an exit while unbonding exists and later withdraw or delegate the same funds twice, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ClearPosition
- Entrypoint: Cosmos SDK MsgClearPosition transaction
- Attacker controls: Owner, PositionId, block time, unbonding state, delegation state
- Exploit idea: clear an exit while unbonding exists and later withdraw or delegate the same funds twice by testing the owner binding angle against `LastKnownBonded` during `trigger exit, cross the unlock boundary, clear position, then add or redelegate`, with specific focus on position primary store.
- Invariant to test: clearing exit claims rewards once and advances checkpoints correctly; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state test for ClearPosition at pre-unlock, exact-unlock, and post-unlock times; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
