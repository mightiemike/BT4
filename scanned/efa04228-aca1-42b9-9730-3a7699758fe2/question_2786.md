# Q2786: SVM send_funds ingest - signature identity address confusion

## Question
Can an unprivileged attacker emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index and use control over transaction signature, log index, slot ordering, and event-type detection from log text so that `parseSendFundsEvent` normalize user-controlled addresses into a different economic target than the source chain intended, breaking the invariant that wrong-type, malformed, or replayed SVM logs never reach terminal vote state and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseSendFundsEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: transaction signature, log index, slot ordering, and event-type detection from log text
- Exploit idea: normalize user-controlled addresses into a different economic target than the source chain intended
- Invariant to test: wrong-type, malformed, or replayed SVM logs never reach terminal vote state
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: emit crafted gateway logs on a local Solana validator and compare raw program data with the resulting `store.Event` JSON and vote message
