# Q3256: SVM send_funds ingest - program data event-type mixup

## Question
Can an unprivileged attacker repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows and use control over base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields so that `parseSendFundsEvent` classify one log as the wrong event type so it enters the wrong confirmation or voting path, breaking the invariant that each `signature:logIndex` pair maps to exactly one canonical event payload and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseSendFundsEvent
- Entrypoint: repeat public SVM gateway traffic around slot finality boundaries or small reorg-equivalent confirmation windows
- Attacker controls: base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields
- Exploit idea: classify one log as the wrong event type so it enters the wrong confirmation or voting path
- Invariant to test: each `signature:logIndex` pair maps to exactly one canonical event payload
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: pack repeated or near-duplicate logs into one signature batch and verify only one canonical local row is created per real event
