# Q1187: SVM parser dispatch - signature identity length truncation

## Question
When an unprivileged actor submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient, does `ParseEvent` remain safe if they control transaction signature, log index, slot ordering, and event-type detection from log text, or can that make it accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, violate the rule that only well-formed SVM gateway bytes can become an inbound or outbound observation, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/event_parser.go:ParseEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: transaction signature, log index, slot ordering, and event-type detection from log text
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: only well-formed SVM gateway bytes can become an inbound or outbound observation
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
