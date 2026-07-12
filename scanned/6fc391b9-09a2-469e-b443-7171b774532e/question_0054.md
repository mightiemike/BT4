# Q54: LockTier owner binding against position delegator account

## Question
Can unprivileged owner account enter through `msgServer.LockTier` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, Id, Amount, ValidatorAddress, focusing on staking share deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then lock funds into a fresh position and immediately use authz or feegrant to exercise owner-controlled follow-up messages, causing `position delegator account` to diverge so the invariant `reward checkpoints begin at the validator's latest event sequence and cannot include pre-position rewards` fails and the attacker can create a position whose indexes disagree with the actual delegation and let rewards or withdrawals route to the wrong owner, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.LockTier
- Entrypoint: Cosmos SDK MsgLockTier transaction
- Attacker controls: Owner, Id, Amount, ValidatorAddress, TriggerExitImmediately, transaction ordering
- Exploit idea: create a position whose indexes disagree with the actual delegation and let rewards or withdrawals route to the wrong owner by testing the owner binding angle against `position delegator account` during `lock funds into a fresh position and immediately use authz or feegrant to exercise owner-controlled follow-up messages`, with specific focus on staking share deltas.
- Invariant to test: reward checkpoints begin at the validator's latest event sequence and cannot include pre-position rewards; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test around LockTier plus bank, staking, distribution, and tieredrewards stores; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
