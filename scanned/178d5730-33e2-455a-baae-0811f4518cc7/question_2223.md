# Q2223: SVM outbound observe - address encoding duplicate signature row

## Question
When an unprivileged actor emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index, does `parseOutboundObservationEvent` remain safe if they control base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields, or can that make it materialize conflicting local rows from the same signature and log index under batched or repeated logs, violate the rule that wrong-type, malformed, or replayed SVM logs never reach terminal vote state, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseOutboundObservationEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: materialize conflicting local rows from the same signature and log index under batched or repeated logs
- Invariant to test: wrong-type, malformed, or replayed SVM logs never reach terminal vote state
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: emit crafted gateway logs on a local Solana validator and compare raw program data with the resulting `store.Event` JSON and vote message
