# Q541: TierRedelegate owner binding against LastKnownBonded

## Question
Can position owner enter through `msgServer.TierRedelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, DstValidator, redelegation timing, focusing on distribution withdraw address routing, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then attempt repeated redelegation with active redelegation mappings still present, causing `LastKnownBonded` to diverge so the invariant `a position has at most one active redelegation and one current validator` fails and the attacker can use instant redelegation from an unbonded source to skip slashing or reward checkpoint updates, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierRedelegate
- Entrypoint: Cosmos SDK MsgTierRedelegate transaction
- Attacker controls: Owner, PositionId, DstValidator, redelegation timing, validator event timing
- Exploit idea: use instant redelegation from an unbonded source to skip slashing or reward checkpoint updates by testing the owner binding angle against `LastKnownBonded` during `attempt repeated redelegation with active redelegation mappings still present`, with specific focus on distribution withdraw address routing.
- Invariant to test: a position has at most one active redelegation and one current validator; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test with two validators, redelegation mappings, hooks, and reward claims; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
