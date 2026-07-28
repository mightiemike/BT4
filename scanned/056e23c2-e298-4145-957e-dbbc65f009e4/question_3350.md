# Q3350: SVM send_funds ingest - program data duplicate signature row

## Question
If a user repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows, can `parseSendFundsEvent` be pushed into a path where base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields causes it to materialize conflicting local rows from the same signature and log index under batched or repeated logs, so that only well-formed SVM gateway bytes can become an inbound or outbound observation no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseSendFundsEvent
- Entrypoint: repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows
- Attacker controls: base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields
- Exploit idea: materialize conflicting local rows from the same signature and log index under batched or repeated logs
- Invariant to test: only well-formed SVM gateway bytes can become an inbound or outbound observation
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: emit crafted gateway logs on a local Solana validator and compare raw program data with the resulting `store.Event` JSON and vote message
