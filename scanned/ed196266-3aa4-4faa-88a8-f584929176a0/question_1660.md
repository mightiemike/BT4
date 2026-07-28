# Q1660: SVM tx payload marshal - program data address confusion

## Question
Can an unprivileged attacker emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index and use control over base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields so that `parseUniversalTxEvent` normalize user-controlled addresses into a different economic target than the source chain intended, breaking the invariant that address normalization never changes the recipient, sender, token, or refund meaning of the event and leading to widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseUniversalTxEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields
- Exploit idea: normalize user-controlled addresses into a different economic target than the source chain intended
- Invariant to test: address normalization never changes the recipient, sender, token, or refund meaning of the event
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
