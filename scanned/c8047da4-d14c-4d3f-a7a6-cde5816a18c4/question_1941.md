# Q1941: SVM outbound observe - address encoding length truncation

## Question
When an unprivileged actor emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index, does `parseOutboundObservationEvent` remain safe if they control base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields, or can that make it accept truncated Solana event data as a valid inbound or outbound observation with partial semantics, violate the rule that each `signature:logIndex` pair maps to exactly one canonical event payload, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseOutboundObservationEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: accept truncated Solana event data as a valid inbound or outbound observation with partial semantics
- Invariant to test: each `signature:logIndex` pair maps to exactly one canonical event payload
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: mutate byte lengths, discriminators, and payload tails and confirm partially decoded logs cannot move beyond parsing
