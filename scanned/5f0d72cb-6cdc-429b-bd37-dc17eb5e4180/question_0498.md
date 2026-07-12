# Q498: TierRedelegate owner binding against LastEventSeq

## Question
Can position owner enter through `msgServer.TierRedelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, DstValidator, redelegation timing, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then claim rewards, redelegate to a new validator, wait for completion hook, then claim again, causing `LastEventSeq` to diverge so the invariant `source and destination validator position counts reconcile exactly` fails and the attacker can claim bonus rewards from both validators for the same time segment, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierRedelegate
- Entrypoint: Cosmos SDK MsgTierRedelegate transaction
- Attacker controls: Owner, PositionId, DstValidator, redelegation timing, validator event timing
- Exploit idea: claim bonus rewards from both validators for the same time segment by testing the owner binding angle against `LastEventSeq` during `claim rewards, redelegate to a new validator, wait for completion hook, then claim again`, with specific focus on staking share deltas.
- Invariant to test: source and destination validator position counts reconcile exactly; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test with two validators, redelegation mappings, hooks, and reward claims; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
