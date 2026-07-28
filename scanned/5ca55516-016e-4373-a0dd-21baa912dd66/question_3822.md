# Q3822: SVM tx payload marshal - tx payload length truncation

## Question
If a user repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows, can `parseUniversalTxEvent` be pushed into a path where amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event causes it to accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, so that each `signature:logIndex` pair maps to exactly one canonical event payload no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseUniversalTxEvent
- Entrypoint: repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows
- Attacker controls: amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: each `signature:logIndex` pair maps to exactly one canonical event payload
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
