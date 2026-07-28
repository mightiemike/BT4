# Q2691: SVM parser dispatch - signature identity length truncation

## Question
Can an unprivileged attacker emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index and use control over transaction signature, log index, slot ordering, and event-type detection from log text so that `ParseEvent` accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, breaking the invariant that address normalization never changes the recipient, sender, token, or refund meaning of the event and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:ParseEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: transaction signature, log index, slot ordering, and event-type detection from log text
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: address normalization never changes the recipient, sender, token, or refund meaning of the event
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
