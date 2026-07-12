# Q930: ClearPosition owner binding against LastEventSeq

## Question
Can position owner enter through `msgServer.ClearPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, unbonding state, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then clear after validator unbond events and then claim rewards, causing `LastEventSeq` to diverge so the invariant `position remains delegated and indexed consistently if exit is cleared` fails and the attacker can cancel an exit after unlock to bypass withdraw-only state and continue earning rewards, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ClearPosition
- Entrypoint: Cosmos SDK MsgClearPosition transaction
- Attacker controls: Owner, PositionId, block time, unbonding state, delegation state
- Exploit idea: cancel an exit after unlock to bypass withdraw-only state and continue earning rewards by testing the owner binding angle against `LastEventSeq` during `clear after validator unbond events and then claim rewards`, with specific focus on staking share deltas.
- Invariant to test: position remains delegated and indexed consistently if exit is cleared; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state test for ClearPosition at pre-unlock, exact-unlock, and post-unlock times; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
