# Q303: TierUndelegate owner binding against position Delegation

## Question
Can position owner enter through `msgServer.TierUndelegate` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, position exit state, focusing on bank balance deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then repeat ClearPosition and TierUndelegate around the exit lock boundary, causing `position Delegation` to diverge so the invariant `base and bonus rewards are claimed once before delegation shares leave the position` fails and the attacker can leave stale position indexes so future claims or exits operate on deleted delegation state, leading to Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.TierUndelegate
- Entrypoint: Cosmos SDK MsgTierUndelegate transaction
- Attacker controls: Owner, PositionId, block time, position exit state, validator state
- Exploit idea: leave stale position indexes so future claims or exits operate on deleted delegation state by testing the owner binding angle against `position Delegation` during `repeat ClearPosition and TierUndelegate around the exit lock boundary`, with specific focus on bank balance deltas.
- Invariant to test: base and bonus rewards are claimed once before delegation shares leave the position; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical direct loss or unauthorized withdrawal of bonded CRO from a tiered rewards position; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper state-machine test over TriggerExitFromTier, TierUndelegate, staking unbonding, and WithdrawFromTier; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
