# Q0720: SVM tx payload marshal - address encoding duplicate signature row

## Question
Can an unprivileged attacker submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient and use control over base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields so that `parseUniversalTxEvent` materialize conflicting local rows from the same signature and log index under batched or repeated logs, breaking the invariant that address normalization never changes the recipient, sender, token, or refund meaning of the event and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseUniversalTxEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: materialize conflicting local rows from the same signature and log index under batched or repeated logs
- Invariant to test: address normalization never changes the recipient, sender, token, or refund meaning of the event
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: emit crafted gateway logs on a local Solana validator and compare raw program data with the resulting `store.Event` JSON and vote message
