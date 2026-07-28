# Q1284: SVM tx payload marshal - signature identity address confusion

## Question
Can an unprivileged attacker submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient and use control over transaction signature, log index, slot ordering, and event-type detection from log text so that `parseUniversalTxEvent` normalize user-controlled addresses into a different economic target than the source chain intended, breaking the invariant that address normalization never changes the recipient, sender, token, or refund meaning of the event and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseUniversalTxEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: transaction signature, log index, slot ordering, and event-type detection from log text
- Exploit idea: normalize user-controlled addresses into a different economic target than the source chain intended
- Invariant to test: address normalization never changes the recipient, sender, token, or refund meaning of the event
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: emit crafted gateway logs on a local Solana validator and compare raw program data with the resulting `store.Event` JSON and vote message
