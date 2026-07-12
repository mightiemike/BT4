# Q956: ClearPosition owner binding against ExitUnlockAt

## Question
Can position owner enter through `msgServer.ClearPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, unbonding state, focusing on distribution withdraw address routing, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then call ClearPosition on a non-triggered position and verify no hidden state changes, causing `ExitUnlockAt` to diverge so the invariant `clearing exit claims rewards once and advances checkpoints correctly` fails and the attacker can clear an exit while unbonding exists and later withdraw or delegate the same funds twice, leading to Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ClearPosition
- Entrypoint: Cosmos SDK MsgClearPosition transaction
- Attacker controls: Owner, PositionId, block time, unbonding state, delegation state
- Exploit idea: clear an exit while unbonding exists and later withdraw or delegate the same funds twice by testing the owner binding angle against `ExitUnlockAt` during `call ClearPosition on a non-triggered position and verify no hidden state changes`, with specific focus on distribution withdraw address routing.
- Invariant to test: clearing exit claims rewards once and advances checkpoints correctly; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state test for ClearPosition at pre-unlock, exact-unlock, and post-unlock times; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
