# Q42: LockTier owner binding against delegation shares

## Question
Can unprivileged owner account enter through `msgServer.LockTier` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, Id, Amount, ValidatorAddress, focusing on bank balance deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then submit several MsgLockTier calls in adjacent blocks with different validators and boundary amounts, causing `delegation shares` to diverge so the invariant `position indexes and validator counts match the single active delegation created for the position` fails and the attacker can capture base rewards by corrupting the distribution withdraw address for the position delegator, leading to Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.LockTier
- Entrypoint: Cosmos SDK MsgLockTier transaction
- Attacker controls: Owner, Id, Amount, ValidatorAddress, TriggerExitImmediately, transaction ordering
- Exploit idea: capture base rewards by corrupting the distribution withdraw address for the position delegator by testing the owner binding angle against `delegation shares` during `submit several MsgLockTier calls in adjacent blocks with different validators and boundary amounts`, with specific focus on bank balance deltas.
- Invariant to test: position indexes and validator counts match the single active delegation created for the position; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test around LockTier plus bank, staking, distribution, and tieredrewards stores; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
