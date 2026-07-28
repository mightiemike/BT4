# Q0532: SVM tx payload marshal - address encoding address confusion

## Question
If a user submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient, can `parseUniversalTxEvent` be pushed into a path where base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields causes it to normalize user-controlled addresses into a different economic target than the source chain intended, so that each `signature:logIndex` pair maps to exactly one canonical event payload no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseUniversalTxEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: normalize user-controlled addresses into a different economic target than the source chain intended
- Invariant to test: each `signature:logIndex` pair maps to exactly one canonical event payload
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: toggle base58, zero bytes, and alternate-length address encodings and inspect whether economic meaning changes after normalization
