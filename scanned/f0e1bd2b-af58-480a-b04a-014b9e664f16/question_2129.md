# Q2129: SVM outbound observe - address encoding event-type mixup

## Question
If a user emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index, can `parseOutboundObservationEvent` be pushed into a path where base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields causes it to classify one log as the wrong event type so it enters the wrong confirmation or voting path, so that address normalization never changes the recipient, sender, token, or refund meaning of the event no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseOutboundObservationEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: classify one log as the wrong event type so it enters the wrong confirmation or voting path
- Invariant to test: address normalization never changes the recipient, sender, token, or refund meaning of the event
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
