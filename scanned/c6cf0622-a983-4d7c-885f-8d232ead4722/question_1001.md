# Q1001: ClearPosition owner binding against LastKnownBonded

## Question
Can position owner enter through `msgServer.ClearPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, unbonding state, focusing on position primary store, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then call ClearPosition on a non-triggered position and verify no hidden state changes, causing `LastKnownBonded` to diverge so the invariant `only owner can clear exit state` fails and the attacker can clear an exit while unbonding exists and later withdraw or delegate the same funds twice, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ClearPosition
- Entrypoint: Cosmos SDK MsgClearPosition transaction
- Attacker controls: Owner, PositionId, block time, unbonding state, delegation state
- Exploit idea: clear an exit while unbonding exists and later withdraw or delegate the same funds twice by testing the owner binding angle against `LastKnownBonded` during `call ClearPosition on a non-triggered position and verify no hidden state changes`, with specific focus on position primary store.
- Invariant to test: only owner can clear exit state; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state test for ClearPosition at pre-unlock, exact-unlock, and post-unlock times; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
