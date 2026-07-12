# Q529: TierRedelegate owner binding against RedelegationMappings

## Question
Can position owner enter through `msgServer.TierRedelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, DstValidator, redelegation timing, focusing on distribution withdraw address routing, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then redelegate near a validator bond or unbond event and check bonus segment boundaries, causing `RedelegationMappings` to diverge so the invariant `bonus reward checkpoints reset to the destination validator's event sequence` fails and the attacker can double count validator position indexes and corrupt voting power or rewards, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierRedelegate
- Entrypoint: Cosmos SDK MsgTierRedelegate transaction
- Attacker controls: Owner, PositionId, DstValidator, redelegation timing, validator event timing
- Exploit idea: double count validator position indexes and corrupt voting power or rewards by testing the owner binding angle against `RedelegationMappings` during `redelegate near a validator bond or unbond event and check bonus segment boundaries`, with specific focus on distribution withdraw address routing.
- Invariant to test: bonus reward checkpoints reset to the destination validator's event sequence; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test with two validators, redelegation mappings, hooks, and reward claims; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
