# Q560: TierRedelegate owner binding against destination validator count

## Question
Can position owner enter through `msgServer.TierRedelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, DstValidator, redelegation timing, focusing on position primary store, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then redelegate when source validator is unbonded and unbondingID is zero, causing `destination validator count` to diverge so the invariant `redelegation mapping links the returned unbondingID to only that position` fails and the attacker can use instant redelegation from an unbonded source to skip slashing or reward checkpoint updates, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierRedelegate
- Entrypoint: Cosmos SDK MsgTierRedelegate transaction
- Attacker controls: Owner, PositionId, DstValidator, redelegation timing, validator event timing
- Exploit idea: use instant redelegation from an unbonded source to skip slashing or reward checkpoint updates by testing the owner binding angle against `destination validator count` during `redelegate when source validator is unbonded and unbondingID is zero`, with specific focus on position primary store.
- Invariant to test: redelegation mapping links the returned unbondingID to only that position; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test with two validators, redelegation mappings, hooks, and reward claims; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
