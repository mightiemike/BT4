# Q0909: SVM raw decode - tx payload address confusion

## Question
When an unprivileged actor submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient, does `decodeUniversalTxEvent` remain safe if they control amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event, or can that make it normalize user-controlled addresses into a different economic target than the source chain intended, violate the rule that wrong-type, malformed, or replayed SVM logs never reach terminal vote state, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_parser.go:decodeUniversalTxEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: amount, payload length, payload bytes, tx type, and gas-used fields inside the decoded event
- Exploit idea: normalize user-controlled addresses into a different economic target than the source chain intended
- Invariant to test: wrong-type, malformed, or replayed SVM logs never reach terminal vote state
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: mutate byte lengths, discriminators, and payload tails and confirm partially decoded logs cannot move beyond parsing
