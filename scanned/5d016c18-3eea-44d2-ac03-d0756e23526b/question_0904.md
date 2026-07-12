# Q904: ClearPosition owner binding against LastBonusAccrual

## Question
Can position owner enter through `msgServer.ClearPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, unbonding state, focusing on bank balance deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then clear exit while an unbonding or redelegation exists for the position delegator, causing `LastBonusAccrual` to diverge so the invariant `clearing after unlock cannot hide active unbonding or undelegated state` fails and the attacker can avoid close-only tier constraints and keep accruing rewards against intended shutdown rules, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ClearPosition
- Entrypoint: Cosmos SDK MsgClearPosition transaction
- Attacker controls: Owner, PositionId, block time, unbonding state, delegation state
- Exploit idea: avoid close-only tier constraints and keep accruing rewards against intended shutdown rules by testing the owner binding angle against `LastBonusAccrual` during `clear exit while an unbonding or redelegation exists for the position delegator`, with specific focus on bank balance deltas.
- Invariant to test: clearing after unlock cannot hide active unbonding or undelegated state; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state test for ClearPosition at pre-unlock, exact-unlock, and post-unlock times; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
