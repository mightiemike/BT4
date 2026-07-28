# Q2850: EVM signing hash build - replay timing nonce collision

## Question
If a user trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data, can `GetOutboundSigningRequest` be pushed into a path where how the same outbound is retried when mempool drops, nonce consumption, or empty tx hashes occur causes it to make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another, so that the `SigningHash` always commits to the exact destination transaction bytes that will be broadcast no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:GetOutboundSigningRequest
- Entrypoint: trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data
- Attacker controls: how the same outbound is retried when mempool drops, nonce consumption, or empty tx hashes occur
- Exploit idea: make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another
- Invariant to test: the `SigningHash` always commits to the exact destination transaction bytes that will be broadcast
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: queue several outbounds to the same chain, drop or replace one EVM tx, and see whether another outbound incorrectly inherits its nonce state
