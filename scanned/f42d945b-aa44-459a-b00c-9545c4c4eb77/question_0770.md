# Q0770: Inbound build - status machine race overwrite

## Question
Can an unprivileged attacker submit a normal inbound transfer whose parsed event reaches the local event database and use control over status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED` so that `constructInbound` overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, breaking the invariant that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/common/event_processor.go:constructInbound
- Entrypoint: submit a normal inbound transfer whose parsed event reaches the local event database
- Attacker controls: status transitions between `PENDING`, `CONFIRMED`, `SIGNED`, `BROADCASTED`, `REVERTED`, and `COMPLETED`
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: advance block height and retention windows while a live event is pending and confirm the cleaner never deletes it early
