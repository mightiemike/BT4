# Q2320: SVM address normalize - tx payload length truncation

## Question
Can an unprivileged attacker emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index and use control over amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event so that `base58ToHex` accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, breaking the invariant that wrong-type, malformed, or replayed SVM logs never reach terminal vote state and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_parser.go:base58ToHex
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: wrong-type, malformed, or replayed SVM logs never reach terminal vote state
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: emit crafted gateway logs on a local Solana validator and compare raw program data with the resulting `store.Event` JSON and vote message
