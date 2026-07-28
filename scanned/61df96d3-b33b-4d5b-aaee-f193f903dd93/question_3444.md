# Q3444: SVM send_funds ingest - address encoding length truncation

## Question
If a user repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows, can `parseSendFundsEvent` be pushed into a path where base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields causes it to accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, so that only well-formed SVM gateway bytes can become an inbound or outbound observation no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseSendFundsEvent
- Entrypoint: repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: only well-formed SVM gateway bytes can become an inbound or outbound observation
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: emit crafted gateway logs on a local Solana validator and compare raw program data with the resulting `store.Event` JSON and vote message
