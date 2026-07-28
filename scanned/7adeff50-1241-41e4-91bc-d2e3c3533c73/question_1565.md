# Q1565: SVM outbound observe - program data length truncation

## Question
Can an unprivileged attacker emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index and use control over base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields so that `parseOutboundObservationEvent` accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, breaking the invariant that only well-formed SVM gateway bytes can become an inbound or outbound observation and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseOutboundObservationEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: only well-formed SVM gateway bytes can become an inbound or outbound observation
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: toggle base58, zero bytes, and alternate-length address encodings and inspect whether economic meaning changes after normalization
