# Q442: TierUndelegate owner binding against position Delegation

## Question
Can position owner enter through `msgServer.TierUndelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, position exit state, focusing on owner/tier/validator indexes, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then undelegate after the validator becomes unbonded or removed and then attempt withdrawal, causing `position Delegation` to diverge so the invariant `only the position owner can begin unbonding for that position` fails and the attacker can leave stale position indexes so future claims or exits operate on deleted delegation state, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierUndelegate
- Entrypoint: Cosmos SDK MsgTierUndelegate transaction
- Attacker controls: Owner, PositionId, block time, position exit state, validator state
- Exploit idea: leave stale position indexes so future claims or exits operate on deleted delegation state by testing the owner binding angle against `position Delegation` during `undelegate after the validator becomes unbonded or removed and then attempt withdrawal`, with specific focus on owner/tier/validator indexes.
- Invariant to test: only the position owner can begin unbonding for that position; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state-machine test over TriggerExitFromTier, TierUndelegate, staking unbonding, and WithdrawFromTier; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
