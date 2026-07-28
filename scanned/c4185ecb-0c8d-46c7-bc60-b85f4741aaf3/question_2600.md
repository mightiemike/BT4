# Q2600: SVM tx payload marshal - tx payload duplicate signature row

## Question
Can an unprivileged attacker emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index and use control over amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event so that `parseUniversalTxEvent` materialize conflicting local rows from the same signature and log index under batched or repeated logs, breaking the invariant that address normalization never changes the recipient, sender, token, or refund meaning of the event and leading to widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseUniversalTxEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event
- Exploit idea: materialize conflicting local rows from the same signature and log index under batched or repeated logs
- Invariant to test: address normalization never changes the recipient, sender, token, or refund meaning of the event
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
