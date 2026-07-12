# Q458: TierRedelegate owner binding against RedelegationMappings

## Question
Can position owner enter through `msgServer.TierRedelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, DstValidator, redelegation timing, focusing on bank balance deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then claim rewards, redelegate to a new validator, wait for completion hook, then claim again, causing `RedelegationMappings` to diverge so the invariant `redelegation cannot revive an expired exit or bypass close-only tier rules` fails and the attacker can use instant redelegation from an unbonded source to skip slashing or reward checkpoint updates, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierRedelegate
- Entrypoint: Cosmos SDK MsgTierRedelegate transaction
- Attacker controls: Owner, PositionId, DstValidator, redelegation timing, validator event timing
- Exploit idea: use instant redelegation from an unbonded source to skip slashing or reward checkpoint updates by testing the owner binding angle against `RedelegationMappings` during `claim rewards, redelegate to a new validator, wait for completion hook, then claim again`, with specific focus on bank balance deltas.
- Invariant to test: redelegation cannot revive an expired exit or bypass close-only tier rules; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test with two validators, redelegation mappings, hooks, and reward claims; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
