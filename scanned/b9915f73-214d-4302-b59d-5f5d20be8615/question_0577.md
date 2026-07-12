# Q577: TierRedelegate owner binding against LastKnownBonded

## Question
Can position owner enter through `msgServer.TierRedelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, DstValidator, redelegation timing, focusing on position primary store, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then trigger exit, redelegate before lock completion, then attempt ExitTierWithDelegation, causing `LastKnownBonded` to diverge so the invariant `redelegation mapping links the returned unbondingID to only that position` fails and the attacker can overwrite a redelegation mapping and cause a slash or completion hook to update the wrong position, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierRedelegate
- Entrypoint: Cosmos SDK MsgTierRedelegate transaction
- Attacker controls: Owner, PositionId, DstValidator, redelegation timing, validator event timing
- Exploit idea: overwrite a redelegation mapping and cause a slash or completion hook to update the wrong position by testing the owner binding angle against `LastKnownBonded` during `trigger exit, redelegate before lock completion, then attempt ExitTierWithDelegation`, with specific focus on position primary store.
- Invariant to test: redelegation mapping links the returned unbondingID to only that position; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test with two validators, redelegation mappings, hooks, and reward claims; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
