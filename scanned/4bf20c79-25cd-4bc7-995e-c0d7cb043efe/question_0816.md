# Q0816: SVM address normalize - tx payload length truncation

## Question
When an unprivileged actor submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient, does `base58ToHex` remain safe if they control amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event, or can that make it accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, violate the rule that address normalization never changes the recipient, sender, token, or refund meaning of the event, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:base58ToHex
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: address normalization never changes the recipient, sender, token, or refund meaning of the event
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: emit crafted gateway logs on a local Solana validator and compare raw program data with the resulting `store.Event` JSON and vote message
