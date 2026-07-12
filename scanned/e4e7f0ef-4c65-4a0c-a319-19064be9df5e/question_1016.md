# Q1016: ClearPosition owner binding against ExitUnlockAt

## Question
Can position owner enter through `msgServer.ClearPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, unbonding state, focusing on owner/tier/validator indexes, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then call ClearPosition on a non-triggered position and verify no hidden state changes, causing `ExitUnlockAt` to diverge so the invariant `tier close-only rules cannot be bypassed by clearing exit` fails and the attacker can preserve stale bonus checkpoints and claim rewards for an already exited segment, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ClearPosition
- Entrypoint: Cosmos SDK MsgClearPosition transaction
- Attacker controls: Owner, PositionId, block time, unbonding state, delegation state
- Exploit idea: preserve stale bonus checkpoints and claim rewards for an already exited segment by testing the owner binding angle against `ExitUnlockAt` during `call ClearPosition on a non-triggered position and verify no hidden state changes`, with specific focus on owner/tier/validator indexes.
- Invariant to test: tier close-only rules cannot be bypassed by clearing exit; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state test for ClearPosition at pre-unlock, exact-unlock, and post-unlock times; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
