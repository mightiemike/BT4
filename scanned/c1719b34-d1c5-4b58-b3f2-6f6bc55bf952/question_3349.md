# Q3349: SVM parser dispatch - program data duplicate signature row

## Question
When an unprivileged actor repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows, does `ParseEvent` remain safe if they control base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields, or can that make it materialize conflicting local rows from the same signature and log index under batched or repeated logs, violate the rule that only well-formed SVM gateway bytes can become an inbound or outbound observation, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/event_parser.go:ParseEvent
- Entrypoint: repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows
- Attacker controls: base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields
- Exploit idea: materialize conflicting local rows from the same signature and log index under batched or repeated logs
- Invariant to test: only well-formed SVM gateway bytes can become an inbound or outbound observation
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: emit crafted gateway logs on a local Solana validator and compare raw program data with the resulting `store.Event` JSON and vote message
