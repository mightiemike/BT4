# Q495: TierRedelegate owner binding against RedelegationMappings

## Question
Can position owner enter through `msgServer.TierRedelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, DstValidator, redelegation timing, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then redelegate when source validator is unbonded and unbondingID is zero, causing `RedelegationMappings` to diverge so the invariant `source and destination validator position counts reconcile exactly` fails and the attacker can overwrite a redelegation mapping and cause a slash or completion hook to update the wrong position, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierRedelegate
- Entrypoint: Cosmos SDK MsgTierRedelegate transaction
- Attacker controls: Owner, PositionId, DstValidator, redelegation timing, validator event timing
- Exploit idea: overwrite a redelegation mapping and cause a slash or completion hook to update the wrong position by testing the owner binding angle against `RedelegationMappings` during `redelegate when source validator is unbonded and unbondingID is zero`, with specific focus on staking share deltas.
- Invariant to test: source and destination validator position counts reconcile exactly; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test with two validators, redelegation mappings, hooks, and reward claims; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
