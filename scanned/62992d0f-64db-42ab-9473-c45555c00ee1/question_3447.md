# Q3447: SVM raw decode - address encoding length truncation

## Question
Can an unprivileged attacker repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows and use control over base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields so that `decodeUniversalTxEvent` accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, breaking the invariant that only well-formed SVM gateway bytes can become an inbound or outbound observation and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_parser.go:decodeUniversalTxEvent
- Entrypoint: repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: only well-formed SVM gateway bytes can become an inbound or outbound observation
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: emit crafted gateway logs on a local Solana validator and compare raw program data with the resulting `store.Event` JSON and vote message
