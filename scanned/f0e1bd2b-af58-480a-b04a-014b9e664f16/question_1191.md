# Q1191: SVM raw decode - signature identity length truncation

## Question
Can an unprivileged attacker submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient and use control over transaction signature, log index, slot ordering, and event-type detection from log text so that `decodeUniversalTxEvent` accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, breaking the invariant that only well-formed SVM gateway bytes can become an inbound or outbound observation and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/event_parser.go:decodeUniversalTxEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: transaction signature, log index, slot ordering, and event-type detection from log text
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: only well-formed SVM gateway bytes can become an inbound or outbound observation
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
