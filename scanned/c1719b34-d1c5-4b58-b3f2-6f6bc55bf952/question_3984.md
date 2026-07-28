# Q3984: EVM gas-used read - hash identity nonce collision

## Question
If a user cause many public Push-chain outbounds to the same EVM chain to queue concurrently, can `GetGasFeeUsed` be pushed into a path where `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row causes it to make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another, so that an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:GetGasFeeUsed
- Entrypoint: cause many public Push-chain outbounds to the same EVM chain to queue concurrently
- Attacker controls: `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row
- Exploit idea: make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another
- Invariant to test: an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force success, revert, not-found, and mempool-drop cases and verify the resolver never marks the wrong terminal outcome
