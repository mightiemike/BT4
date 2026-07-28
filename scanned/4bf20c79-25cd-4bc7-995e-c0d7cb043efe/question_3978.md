# Q3978: EVM signing hash build - hash identity nonce collision

## Question
Can an unprivileged attacker cause many public Push-chain outbounds to the same EVM chain to queue concurrently and use control over `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row so that `GetOutboundSigningRequest` make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another, breaking the invariant that an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:GetOutboundSigningRequest
- Entrypoint: cause many public Push-chain outbounds to the same EVM chain to queue concurrently
- Attacker controls: `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row
- Exploit idea: make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another
- Invariant to test: an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force success, revert, not-found, and mempool-drop cases and verify the resolver never marks the wrong terminal outcome
