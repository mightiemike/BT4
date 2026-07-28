# Q0814: SVM tx payload marshal - tx payload length truncation

## Question
Can an unprivileged attacker submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient and use control over amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event so that `parseUniversalTxEvent` accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, breaking the invariant that address normalization never changes the recipient, sender, token, or refund meaning of the event and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseUniversalTxEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: address normalization never changes the recipient, sender, token, or refund meaning of the event
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: emit crafted gateway logs on a local Solana validator and compare raw program data with the resulting `store.Event` JSON and vote message
