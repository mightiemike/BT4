# Q1046: EVM pending confirm - value fields double record

## Question
When an unprivileged actor submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields, does `processPendingEvents` remain safe if they control token, amount, revert recipient, tx type, and `fromCEA` bits encoded in the event payload, or can that make it create duplicate or conflicting local records that later produce double voting, double execution, or a permanent stuck retry loop, violate the rule that the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/event_confirmer.go:processPendingEvents
- Entrypoint: submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields
- Attacker controls: token, amount, revert recipient, tx type, and `fromCEA` bits encoded in the event payload
- Exploit idea: create duplicate or conflicting local records that later produce double voting, double execution, or a permanent stuck retry loop
- Invariant to test: the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: mutate offsets, topic counts, and trailing words, then diff parsed `EventData` against the original ABI payload before and after `constructInbound`
