# Q2692: SVM send_funds ingest - signature identity length truncation

## Question
When an unprivileged actor emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index, does `parseSendFundsEvent` remain safe if they control transaction signature, log index, slot ordering, and event-type detection from log text, or can that make it accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, violate the rule that address normalization never changes the recipient, sender, token, or refund meaning of the event, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseSendFundsEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: transaction signature, log index, slot ordering, and event-type detection from log text
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: address normalization never changes the recipient, sender, token, or refund meaning of the event
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
