# Q1192: SVM address normalize - signature identity length truncation

## Question
If a user submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient, can `base58ToHex` be pushed into a path where transaction signature, log index, slot ordering, and event-type detection from log text causes it to accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, so that only well-formed SVM gateway bytes can become an inbound or outbound observation no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/event_parser.go:base58ToHex
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: transaction signature, log index, slot ordering, and event-type detection from log text
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: only well-formed SVM gateway bytes can become an inbound or outbound observation
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
