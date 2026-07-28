# Q0784: EVM call encoding - hash identity sign/broadcast mismatch

## Question
If a user create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields, can `encodeFunctionCall` be pushed into a path where `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row causes it to produce a signed hash that does not bind the exact destination transaction later broadcast to the EVM chain, so that an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:encodeFunctionCall
- Entrypoint: create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields
- Attacker controls: `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row
- Exploit idea: produce a signed hash that does not bind the exact destination transaction later broadcast to the EVM chain
- Invariant to test: an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: queue several outbounds to the same chain, drop or replace one EVM tx, and see whether another outbound incorrectly inherits its nonce state
