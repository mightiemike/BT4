# Q1133: ClaimTierRewards owner binding against LastKnownBonded

## Question
Can position owner enter through `msgServer.ClaimTierRewards` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionIds, ordering, validator events, focusing on distribution withdraw address routing, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then claim after redelegation and then after completion hook updates the mapping, causing `LastKnownBonded` to diverge so the invariant `base rewards are routed only to the position owner` fails and the attacker can combine redelegation and claim timing to earn rewards from two validator histories for one stake, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ClaimTierRewards
- Entrypoint: Cosmos SDK MsgClaimTierRewards transaction
- Attacker controls: Owner, PositionIds, ordering, validator events, claim batching
- Exploit idea: combine redelegation and claim timing to earn rewards from two validator histories for one stake by testing the owner binding angle against `LastKnownBonded` during `claim after redelegation and then after completion hook updates the mapping`, with specific focus on distribution withdraw address routing.
- Invariant to test: base rewards are routed only to the position owner; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper fuzz/state-machine test over ClaimTierRewards with validator events and repeated batches; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
