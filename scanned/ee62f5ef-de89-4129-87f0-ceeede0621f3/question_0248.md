# Q0248: SVM send_funds ingest - program data event-type mixup

## Question
When an unprivileged actor submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient, does `parseSendFundsEvent` remain safe if they control base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields, or can that make it classify one log as the wrong event type so it enters the wrong confirmation or voting path, violate the rule that address normalization never changes the recipient, sender, token, or refund meaning of the event, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_parser.go:parseSendFundsEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: base64-encoded `Program data:` bytes, including lengths, discriminators, and trailing fields
- Exploit idea: classify one log as the wrong event type so it enters the wrong confirmation or voting path
- Invariant to test: address normalization never changes the recipient, sender, token, or refund meaning of the event
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: emit crafted gateway logs on a local Solana validator and compare raw program data with the resulting `store.Event` JSON and vote message
