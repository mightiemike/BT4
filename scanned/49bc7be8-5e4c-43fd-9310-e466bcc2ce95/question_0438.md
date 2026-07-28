# Q0438: SVM tx payload marshal - address encoding length truncation

## Question
If a user submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient, can `parseUniversalTxEvent` be pushed into a path where base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields causes it to accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, so that wrong-type, malformed, or replayed SVM logs never reach terminal vote state no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseUniversalTxEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: wrong-type, malformed, or replayed SVM logs never reach terminal vote state
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: mutate byte lengths, discriminators, and payload tails and confirm partially decoded logs cannot move beyond parsing
