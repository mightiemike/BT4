# Q0534: SVM address normalize - address encoding address confusion

## Question
Can an unprivileged attacker submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient and use control over base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields so that `base58ToHex` normalize user-controlled addresses into a different economic target than the source chain intended, breaking the invariant that each `signature:logIndex` pair maps to exactly one canonical event payload and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:base58ToHex
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: normalize user-controlled addresses into a different economic target than the source chain intended
- Invariant to test: each `signature:logIndex` pair maps to exactly one canonical event payload
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: toggle base58, zero bytes, and alternate-length address encodings and inspect whether economic meaning changes after normalization
