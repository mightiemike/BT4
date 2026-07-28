# Q3257: SVM outbound observe - program data event-type mixup

## Question
When an unprivileged actor repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows, does `parseOutboundObservationEvent` remain safe if they control base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields, or can that make it classify one log as the wrong event type so it enters the wrong confirmation or voting path, violate the rule that each `signature:logIndex` pair maps to exactly one canonical event payload, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseOutboundObservationEvent
- Entrypoint: repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows
- Attacker controls: base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields
- Exploit idea: classify one log as the wrong event type so it enters the wrong confirmation or voting path
- Invariant to test: each `signature:logIndex` pair maps to exactly one canonical event payload
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
