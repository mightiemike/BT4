# Q1123: Median write and stored state diverge after an EVM-call edge case via Repeated Votes Vote Updates / Vote-Processing Runs In Normal in Keeper.SetChainMeta

## Question
Can an unprivileged attacker enter through a gasless `MsgVoteChainMeta` if signer checks can be bypassed, or any user flow whose settlement depends on chain-meta values with repeated votes or vote updates that stress median and staleness logic when vote-processing runs in normal block execution, and cause `Keeper.SetChainMeta` to trigger an unsafe state-transition edge case, so that it leave the on-chain oracle and stored chain-meta record disagreeing about the last applied height or price, breaking the invariant that chain-meta storage and the EVM oracle must advance atomically to one consistent value, and resulting in Permanent freezing of funds or wrong-fee theft?

## Target
- File/function: x/uexecutor/keeper/chain_meta.go::Keeper.SetChainMeta
- Entrypoint: a gasless `MsgVoteChainMeta` if signer checks can be bypassed, or any user flow whose settlement depends on chain-meta values
- Attacker controls: repeated votes or vote updates that stress median and staleness logic
- Exploit idea: Cause `Keeper.SetChainMeta` to trigger an unsafe state-transition edge case, so it can leave the on-chain oracle and stored chain-meta record disagreeing about the last applied height or price.
- Invariant to test: chain-meta storage and the EVM oracle must advance atomically to one consistent value
- Expected Immunefi impact: Permanent freezing of funds or wrong-fee theft
- Fast validation: write a keeper test that records the crafted votes or settlement context and inspect the stored median, EVM write, and downstream fee effects
