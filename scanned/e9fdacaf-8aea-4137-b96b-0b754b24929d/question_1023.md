# Q1023: ClearPosition owner binding against LastBonusAccrual

## Question
Can position owner enter through `msgServer.ClearPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, unbonding state, focusing on owner/tier/validator indexes, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then trigger exit, cross the unlock boundary, clear position, then add or redelegate, causing `LastBonusAccrual` to diverge so the invariant `position remains delegated and indexed consistently if exit is cleared` fails and the attacker can preserve stale bonus checkpoints and claim rewards for an already exited segment, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ClearPosition
- Entrypoint: Cosmos SDK MsgClearPosition transaction
- Attacker controls: Owner, PositionId, block time, unbonding state, delegation state
- Exploit idea: preserve stale bonus checkpoints and claim rewards for an already exited segment by testing the owner binding angle against `LastBonusAccrual` during `trigger exit, cross the unlock boundary, clear position, then add or redelegate`, with specific focus on owner/tier/validator indexes.
- Invariant to test: position remains delegated and indexed consistently if exit is cleared; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state test for ClearPosition at pre-unlock, exact-unlock, and post-unlock times; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
