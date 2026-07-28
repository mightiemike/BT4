# Q0627: SVM raw decode - address encoding event-type mixup

## Question
Can an unprivileged attacker submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient and use control over base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields so that `decodeUniversalTxEvent` classify one log as the wrong event type so it enters the wrong confirmation or voting path, breaking the invariant that only well-formed SVM gateway bytes can become an inbound or outbound observation and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:decodeUniversalTxEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: classify one log as the wrong event type so it enters the wrong confirmation or voting path
- Invariant to test: only well-formed SVM gateway bytes can become an inbound or outbound observation
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
