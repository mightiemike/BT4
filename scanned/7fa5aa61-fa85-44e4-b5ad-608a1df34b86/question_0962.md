# Q962: ClearPosition owner binding against LastEventSeq

## Question
Can position owner enter through `msgServer.ClearPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, unbonding state, focusing on distribution withdraw address routing, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then clear exit around tier close-only update and attempt to keep a position alive, causing `LastEventSeq` to diverge so the invariant `tier close-only rules cannot be bypassed by clearing exit` fails and the attacker can clear another user's exit to block their withdrawal or redirect rewards through later state, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ClearPosition
- Entrypoint: Cosmos SDK MsgClearPosition transaction
- Attacker controls: Owner, PositionId, block time, unbonding state, delegation state
- Exploit idea: clear another user's exit to block their withdrawal or redirect rewards through later state by testing the owner binding angle against `LastEventSeq` during `clear exit around tier close-only update and attempt to keep a position alive`, with specific focus on distribution withdraw address routing.
- Invariant to test: tier close-only rules cannot be bypassed by clearing exit; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state test for ClearPosition at pre-unlock, exact-unlock, and post-unlock times; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
