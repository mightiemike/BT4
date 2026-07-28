# Q0624: SVM send_funds ingest - address encoding event-type mixup

## Question
If a user submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient, can `parseSendFundsEvent` be pushed into a path where base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields causes it to classify one log as the wrong event type so it enters the wrong confirmation or voting path, so that only well-formed SVM gateway bytes can become an inbound or outbound observation no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseSendFundsEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: classify one log as the wrong event type so it enters the wrong confirmation or voting path
- Invariant to test: only well-formed SVM gateway bytes can become an inbound or outbound observation
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
