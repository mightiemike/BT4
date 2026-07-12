# Q1095: ClaimTierRewards owner binding against LastBonusAccrual

## Question
Can position owner enter through `msgServer.ClaimTierRewards` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionIds, ordering, validator events, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then claim after exit trigger but before and after undelegation, causing `LastBonusAccrual` to diverge so the invariant `each base and bonus reward segment is paid once per position` fails and the attacker can claim rewards from another user's position by confusing owner checks or authz signer fields, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.ClaimTierRewards
- Entrypoint: Cosmos SDK MsgClaimTierRewards transaction
- Attacker controls: Owner, PositionIds, ordering, validator events, claim batching
- Exploit idea: claim rewards from another user's position by confusing owner checks or authz signer fields by testing the owner binding angle against `LastBonusAccrual` during `claim after exit trigger but before and after undelegation`, with specific focus on staking share deltas.
- Invariant to test: each base and bonus reward segment is paid once per position; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper fuzz/state-machine test over ClaimTierRewards with validator events and repeated batches; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
