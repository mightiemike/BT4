# Q2902: Height monotonicity can be bypassed around bootstrap boundaries via Repeated Votes Vote Updates / Vote-Processing Runs In Normal in Keeper.PruneValidatorVotes

## Question
Can an unprivileged attacker enter through a gasless `MsgVoteChainMeta` if signer checks can be bypassed, or any user flow whose settlement depends on chain-meta values with repeated votes or vote updates that stress median and staleness logic when vote-processing runs in normal block execution, and cause `Keeper.PruneValidatorVotes` to push the wrong logical object through a vote or terminal state transition, so that it submit a value that should be stale but is accepted because the state looks unbootstrapped or partially bootstrapped, breaking the invariant that chain heights must advance monotonically once a chain is live for users, and resulting in Permanent freezing of funds or wrong outbound finality assumptions?

## Target
- File/function: x/uexecutor/keeper/gas_price.go::Keeper.PruneValidatorVotes
- Entrypoint: a gasless `MsgVoteChainMeta` if signer checks can be bypassed, or any user flow whose settlement depends on chain-meta values
- Attacker controls: repeated votes or vote updates that stress median and staleness logic
- Exploit idea: Cause `Keeper.PruneValidatorVotes` to push the wrong logical object through a vote or terminal state transition, so it can submit a value that should be stale but is accepted because the state looks unbootstrapped or partially bootstrapped.
- Invariant to test: chain heights must advance monotonically once a chain is live for users
- Expected Immunefi impact: Permanent freezing of funds or wrong outbound finality assumptions
- Fast validation: write a keeper test that records the crafted votes or settlement context and inspect the stored median, EVM write, and downstream fee effects
