# Q1847: SVM outbound observe - program data duplicate signature row

## Question
When an unprivileged actor emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index, does `parseOutboundObservationEvent` remain safe if they control base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields, or can that make it materialize conflicting local rows from the same signature and log index under batched or repeated logs, violate the rule that each `signature:logIndex` pair maps to exactly one canonical event payload, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseOutboundObservationEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields
- Exploit idea: materialize conflicting local rows from the same signature and log index under batched or repeated logs
- Invariant to test: each `signature:logIndex` pair maps to exactly one canonical event payload
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: mutate byte lengths, discriminators, and payload tails and confirm partially decoded logs cannot move beyond parsing
