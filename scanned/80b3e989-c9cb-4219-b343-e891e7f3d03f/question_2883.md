# Q2883: SVM raw decode - signature identity event-type mixup

## Question
When an unprivileged actor emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index, does `decodeUniversalTxEvent` remain safe if they control transaction signature, log index, slot ordering, and event-type detection from log text, or can that make it classify one log as the wrong event type so it enters the wrong confirmation or voting path, violate the rule that each `signature:logIndex` pair maps to exactly one canonical event payload, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_parser.go:decodeUniversalTxEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: transaction signature, log index, slot ordering, and event-type detection from log text
- Exploit idea: classify one log as the wrong event type so it enters the wrong confirmation or voting path
- Invariant to test: each `signature:logIndex` pair maps to exactly one canonical event payload
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: mutate byte lengths, discriminators, and payload tails and confirm partially decoded logs cannot move beyond parsing
