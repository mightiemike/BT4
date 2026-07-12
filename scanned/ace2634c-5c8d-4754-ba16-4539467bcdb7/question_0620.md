# Q620: AddToTierPosition owner binding against reward checkpoints

## Question
Can position owner enter through `msgServer.AddToTierPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, position exit state, focusing on bank balance deltas, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then attempt add after trigger exit and then clear exit in the same scenario, causing `reward checkpoints` to diverge so the invariant `owner funds move into the position delegator exactly once before delegation` fails and the attacker can corrupt reward checkpoints so base or bonus rewards are paid twice, leading to High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.AddToTierPosition
- Entrypoint: Cosmos SDK MsgAddToTierPosition transaction
- Attacker controls: Owner, PositionId, Amount, position exit state, validator exchange rate
- Exploit idea: corrupt reward checkpoints so base or bonus rewards are paid twice by testing the owner binding angle against `reward checkpoints` during `attempt add after trigger exit and then clear exit in the same scenario`, with specific focus on bank balance deltas.
- Invariant to test: owner funds move into the position delegator exactly once before delegation; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: High cross-module staking, distribution, and tieredrewards accounting corruption with fund-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over AddToTierPosition, claimRewards, delegate, and position state conversion; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
