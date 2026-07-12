# Q946: ClearPosition owner binding against position delegation

## Question
Can position owner enter through `msgServer.ClearPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, unbonding state, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then call ClearPosition on a non-triggered position and verify no hidden state changes, causing `position delegation` to diverge so the invariant `clearing exit claims rewards once and advances checkpoints correctly` fails and the attacker can avoid close-only tier constraints and keep accruing rewards against intended shutdown rules, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ClearPosition
- Entrypoint: Cosmos SDK MsgClearPosition transaction
- Attacker controls: Owner, PositionId, block time, unbonding state, delegation state
- Exploit idea: avoid close-only tier constraints and keep accruing rewards against intended shutdown rules by testing the owner binding angle against `position delegation` during `call ClearPosition on a non-triggered position and verify no hidden state changes`, with specific focus on staking share deltas.
- Invariant to test: clearing exit claims rewards once and advances checkpoints correctly; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state test for ClearPosition at pre-unlock, exact-unlock, and post-unlock times; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
