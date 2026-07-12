# Q596: TierRedelegate owner binding against destination validator count

## Question
Can position owner enter through `msgServer.TierRedelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, DstValidator, redelegation timing, focusing on owner/tier/validator indexes, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then attempt repeated redelegation with active redelegation mappings still present, causing `destination validator count` to diverge so the invariant `bonus reward checkpoints reset to the destination validator's event sequence` fails and the attacker can claim bonus rewards from both validators for the same time segment, leading to Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierRedelegate
- Entrypoint: Cosmos SDK MsgTierRedelegate transaction
- Attacker controls: Owner, PositionId, DstValidator, redelegation timing, validator event timing
- Exploit idea: claim bonus rewards from both validators for the same time segment by testing the owner binding angle against `destination validator count` during `attempt repeated redelegation with active redelegation mappings still present`, with specific focus on owner/tier/validator indexes.
- Invariant to test: bonus reward checkpoints reset to the destination validator's event sequence; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test with two validators, redelegation mappings, hooks, and reward claims; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
