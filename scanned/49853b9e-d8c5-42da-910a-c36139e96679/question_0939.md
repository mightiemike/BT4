# Q939: ClearPosition owner binding against LastKnownBonded

## Question
Can position owner enter through `msgServer.ClearPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, unbonding state, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then clear exit while an unbonding or redelegation exists for the position delegator, causing `LastKnownBonded` to diverge so the invariant `clearing after unlock cannot hide active unbonding or undelegated state` fails and the attacker can cancel an exit after unlock to bypass withdraw-only state and continue earning rewards, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ClearPosition
- Entrypoint: Cosmos SDK MsgClearPosition transaction
- Attacker controls: Owner, PositionId, block time, unbonding state, delegation state
- Exploit idea: cancel an exit after unlock to bypass withdraw-only state and continue earning rewards by testing the owner binding angle against `LastKnownBonded` during `clear exit while an unbonding or redelegation exists for the position delegator`, with specific focus on staking share deltas.
- Invariant to test: clearing after unlock cannot hide active unbonding or undelegated state; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state test for ClearPosition at pre-unlock, exact-unlock, and post-unlock times; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
