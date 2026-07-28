# Q3728: SVM tx payload marshal - address encoding duplicate signature row

## Question
If a user repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows, can `parseUniversalTxEvent` be pushed into a path where base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields causes it to materialize conflicting local rows from the same signature and log index under batched or repeated logs, so that each `signature:logIndex` pair maps to exactly one canonical event payload no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseUniversalTxEvent
- Entrypoint: repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: materialize conflicting local rows from the same signature and log index under batched or repeated logs
- Invariant to test: each `signature:logIndex` pair maps to exactly one canonical event payload
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
