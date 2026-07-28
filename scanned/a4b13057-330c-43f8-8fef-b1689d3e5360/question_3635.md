# Q3635: SVM raw decode - address encoding event-type mixup

## Question
When an unprivileged actor repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows, does `decodeUniversalTxEvent` remain safe if they control base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields, or can that make it classify one log as the wrong event type so it enters the wrong confirmation or voting path, violate the rule that wrong-type, malformed, or replayed SVM logs never reach terminal vote state, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/svm/event_parser.go:decodeUniversalTxEvent
- Entrypoint: repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: classify one log as the wrong event type so it enters the wrong confirmation or voting path
- Invariant to test: wrong-type, malformed, or replayed SVM logs never reach terminal vote state
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: toggle base58, zero bytes, and alternate-length address encodings and inspect whether economic meaning changes after normalization
