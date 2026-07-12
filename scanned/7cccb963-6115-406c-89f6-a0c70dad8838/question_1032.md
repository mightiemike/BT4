# Q1032: ClearPosition owner binding against LastKnownBonded

## Question
Can position owner enter through `msgServer.ClearPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, unbonding state, focusing on owner/tier/validator indexes, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then clear exit around tier close-only update and attempt to keep a position alive, causing `LastKnownBonded` to diverge so the invariant `clearing exit claims rewards once and advances checkpoints correctly` fails and the attacker can avoid close-only tier constraints and keep accruing rewards against intended shutdown rules, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ClearPosition
- Entrypoint: Cosmos SDK MsgClearPosition transaction
- Attacker controls: Owner, PositionId, block time, unbonding state, delegation state
- Exploit idea: avoid close-only tier constraints and keep accruing rewards against intended shutdown rules by testing the owner binding angle against `LastKnownBonded` during `clear exit around tier close-only update and attempt to keep a position alive`, with specific focus on owner/tier/validator indexes.
- Invariant to test: clearing exit claims rewards once and advances checkpoints correctly; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state test for ClearPosition at pre-unlock, exact-unlock, and post-unlock times; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
