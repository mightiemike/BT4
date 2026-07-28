# Q3916: SVM tx payload marshal - tx payload address confusion

## Question
When an unprivileged actor repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows, does `parseUniversalTxEvent` remain safe if they control amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event, or can that make it normalize user-controlled addresses into a different economic target than the source chain intended, violate the rule that only well-formed SVM gateway bytes can become an inbound or outbound observation, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseUniversalTxEvent
- Entrypoint: repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows
- Attacker controls: amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event
- Exploit idea: normalize user-controlled addresses into a different economic target than the source chain intended
- Invariant to test: only well-formed SVM gateway bytes can become an inbound or outbound observation
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: emit crafted gateway logs on a local Solana validator and compare raw program data with the resulting `store.Event` JSON and vote message
