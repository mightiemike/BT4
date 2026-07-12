# Q1207: WithdrawFromTier owner binding against PositionsByOwner

## Question
Can position owner enter through `msgServer.WithdrawFromTier` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, block time, delegator spendable balance, focusing on bank balance deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then withdraw after partial ExitTierWithDelegation leaves dust in the delegator account, causing `PositionsByOwner` to diverge so the invariant `base reward routing is removed so future rewards cannot leak` fails and the attacker can use exact-time boundary checks to withdraw before exit duration or unbonding completion, leading to High repeated or inflated base or bonus reward claim with direct economic loss?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.WithdrawFromTier
- Entrypoint: Cosmos SDK MsgWithdrawFromTier transaction
- Attacker controls: Owner, PositionId, block time, delegator spendable balance, unbonding state
- Exploit idea: use exact-time boundary checks to withdraw before exit duration or unbonding completion by testing the owner binding angle against `PositionsByOwner` during `withdraw after partial ExitTierWithDelegation leaves dust in the delegator account`, with specific focus on bank balance deltas.
- Invariant to test: base reward routing is removed so future rewards cannot leak; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High repeated or inflated base or bonus reward claim with direct economic loss; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: app integration test with staking unbonding completion and bank balance assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
