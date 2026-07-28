# Q2100: EVM call encoding - value encoding nonce collision

## Question
If a user trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data, can `encodeFunctionCall` be pushed into a path where recipient, asset address, amount, gas price, gas limit, and revert message fields encoded into the destination call causes it to make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another, so that an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:encodeFunctionCall
- Entrypoint: trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data
- Attacker controls: recipient, asset address, amount, gas price, gas limit, and revert message fields encoded into the destination call
- Exploit idea: make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another
- Invariant to test: an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: for one crafted outbound, rebuild the unsigned request and final transaction bytes and prove the hash, nonce, and calldata stay identical across retries
