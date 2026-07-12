# Q88: LockTier owner binding against delegation shares

## Question
Can unprivileged owner account enter through `msgServer.LockTier` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, Id, Amount, ValidatorAddress, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then lock exactly tier minimum amount, then trigger validator status and reward checkpoint changes before claim, causing `delegation shares` to diverge so the invariant `reward checkpoints begin at the validator's latest event sequence and cannot include pre-position rewards` fails and the attacker can capture base rewards by corrupting the distribution withdraw address for the position delegator, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.LockTier
- Entrypoint: Cosmos SDK MsgLockTier transaction
- Attacker controls: Owner, Id, Amount, ValidatorAddress, TriggerExitImmediately, transaction ordering
- Exploit idea: capture base rewards by corrupting the distribution withdraw address for the position delegator by testing the owner binding angle against `delegation shares` during `lock exactly tier minimum amount, then trigger validator status and reward checkpoint changes before claim`, with specific focus on staking share deltas.
- Invariant to test: reward checkpoints begin at the validator's latest event sequence and cannot include pre-position rewards; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test around LockTier plus bank, staking, distribution, and tieredrewards stores; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
