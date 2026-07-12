# Q1100: ClaimTierRewards owner binding against LastKnownBonded

## Question
Can position owner enter through `msgServer.ClaimTierRewards` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionIds, ordering, validator events, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then claim after exit trigger but before and after undelegation, causing `LastKnownBonded` to diverge so the invariant `base rewards are routed only to the position owner` fails and the attacker can claim rewards from another user's position by confusing owner checks or authz signer fields, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ClaimTierRewards
- Entrypoint: Cosmos SDK MsgClaimTierRewards transaction
- Attacker controls: Owner, PositionIds, ordering, validator events, claim batching
- Exploit idea: claim rewards from another user's position by confusing owner checks or authz signer fields by testing the owner binding angle against `LastKnownBonded` during `claim after exit trigger but before and after undelegation`, with specific focus on staking share deltas.
- Invariant to test: base rewards are routed only to the position owner; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper fuzz/state-machine test over ClaimTierRewards with validator events and repeated batches; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
