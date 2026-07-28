# Q1003: SVM raw decode - tx payload event-type mixup

## Question
When an unprivileged actor submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient, does `decodeUniversalTxEvent` remain safe if they control amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event, or can that make it classify one log as the wrong event type so it enters the wrong confirmation or voting path, violate the rule that each `signature:logIndex` pair maps to exactly one canonical event payload, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_parser.go:decodeUniversalTxEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event
- Exploit idea: classify one log as the wrong event type so it enters the wrong confirmation or voting path
- Invariant to test: each `signature:logIndex` pair maps to exactly one canonical event payload
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: toggle base58, zero bytes, and alternate-length address encodings and inspect whether economic meaning changes after normalization
