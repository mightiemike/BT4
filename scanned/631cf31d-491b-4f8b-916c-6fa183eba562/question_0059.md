# Q0059: SVM parser dispatch - program data length truncation

## Question
If a user submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient, can `ParseEvent` be pushed into a path where base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields causes it to accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, so that each `signature:logIndex` pair maps to exactly one canonical event payload no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:ParseEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: each `signature:logIndex` pair maps to exactly one canonical event payload
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: toggle base58, zero bytes, and alternate-length address encodings and inspect whether economic meaning changes after normalization
