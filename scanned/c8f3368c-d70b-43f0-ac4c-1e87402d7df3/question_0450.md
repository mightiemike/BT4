# Q450: TierRedelegate owner binding against source validator count

## Question
Can position owner enter through `msgServer.TierRedelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, DstValidator, redelegation timing, focusing on bank balance deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then redelegate when source validator is unbonded and unbondingID is zero, causing `source validator count` to diverge so the invariant `bonus reward checkpoints reset to the destination validator's event sequence` fails and the attacker can redelegate during exit to avoid lock restrictions and recover delegated stake early, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierRedelegate
- Entrypoint: Cosmos SDK MsgTierRedelegate transaction
- Attacker controls: Owner, PositionId, DstValidator, redelegation timing, validator event timing
- Exploit idea: redelegate during exit to avoid lock restrictions and recover delegated stake early by testing the owner binding angle against `source validator count` during `redelegate when source validator is unbonded and unbondingID is zero`, with specific focus on bank balance deltas.
- Invariant to test: bonus reward checkpoints reset to the destination validator's event sequence; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test with two validators, redelegation mappings, hooks, and reward claims; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
