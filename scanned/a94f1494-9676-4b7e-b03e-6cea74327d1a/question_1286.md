# Q1286: SVM address normalize - signature identity address confusion

## Question
When an unprivileged actor submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient, does `base58ToHex` remain safe if they control transaction signature, log index, slot ordering, and event-type detection from log text, or can that make it normalize user-controlled addresses into a different economic target than the source chain intended, violate the rule that address normalization never changes the recipient, sender, token, or refund meaning of the event, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:base58ToHex
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: transaction signature, log index, slot ordering, and event-type detection from log text
- Exploit idea: normalize user-controlled addresses into a different economic target than the source chain intended
- Invariant to test: address normalization never changes the recipient, sender, token, or refund meaning of the event
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: emit crafted gateway logs on a local Solana validator and compare raw program data with the resulting `store.Event` JSON and vote message
