# Q1379: SVM raw decode - signature identity event-type mixup

## Question
When an unprivileged actor submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient, does `decodeUniversalTxEvent` remain safe if they control transaction signature, log index, slot ordering, and event-type detection from log text, or can that make it classify one log as the wrong event type so it enters the wrong confirmation or voting path, violate the rule that wrong-type, malformed, or replayed SVM logs never reach terminal vote state, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_parser.go:decodeUniversalTxEvent
- Entrypoint: submit a public SVM gateway `send_funds` instruction with attacker-controlled accounts, amount, payload, and revert recipient
- Attacker controls: transaction signature, log index, slot ordering, and event-type detection from log text
- Exploit idea: classify one log as the wrong event type so it enters the wrong confirmation or voting path
- Invariant to test: wrong-type, malformed, or replayed SVM logs never reach terminal vote state
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: mutate byte lengths, discriminators, and payload tails and confirm partially decoded logs cannot move beyond parsing
