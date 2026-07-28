# Q1095: SVM outbound observe - tx payload duplicate signature row

## Question
Can an unprivileged attacker submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient and use control over amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event so that `parseOutboundObservationEvent` materialize conflicting local rows from the same signature and log index under batched or repeated logs, breaking the invariant that only well-formed SVM gateway bytes can become an inbound or outbound observation and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseOutboundObservationEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event
- Exploit idea: materialize conflicting local rows from the same signature and log index under batched or repeated logs
- Invariant to test: only well-formed SVM gateway bytes can become an inbound or outbound observation
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
