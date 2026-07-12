# Q707: AddToTierPosition owner binding against reward checkpoints

## Question
Can position owner enter through `msgServer.AddToTierPosition` by set signer, owner, grantee, or position owner fields to edge-case but valid account combinations while controlling Owner, PositionId, Amount, position exit state, focusing on position primary store, under the precondition that a valuable tiered rewards position or delegation exists under normal production rules, then claim pending rewards, add a boundary amount, and immediately claim again, causing `reward checkpoints` to diverge so the invariant `owner funds move into the position delegator exactly once before delegation` fails and the attacker can make added stake accrue rewards for time before it was locked, leading to Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining?

## Target
- File/function: x/tieredrewards/keeper/msg_server.go::msgServer.AddToTierPosition
- Entrypoint: Cosmos SDK MsgAddToTierPosition transaction
- Attacker controls: Owner, PositionId, Amount, position exit state, validator exchange rate
- Exploit idea: make added stake accrue rewards for time before it was locked by testing the owner binding angle against `reward checkpoints` during `claim pending rewards, add a boundary amount, and immediately claim again`, with specific focus on position primary store.
- Invariant to test: owner funds move into the position delegator exactly once before delegation; additionally, owner, signer, distribution withdraw address, and position owner cannot diverge.
- Expected Immunefi impact: Critical tiered rewards position takeover, delegated-stake theft, or reward-pool draining; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over AddToTierPosition, claimRewards, delegate, and position state conversion; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
