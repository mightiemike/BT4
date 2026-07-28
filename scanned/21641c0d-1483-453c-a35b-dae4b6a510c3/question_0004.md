# Q0004: EVM universal-tx decode - topic binding partial decode

## Question
When an unprivileged actor submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields, does `parseUniversalTx` remain safe if they control indexed topics for sender, recipient, tx hash, and log index, or can that make it accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, violate the rule that every observed EVM log maps to exactly one canonical `EventData` payload and one confirmation policy, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/evm/event_parser.go:parseUniversalTx
- Entrypoint: submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields
- Attacker controls: indexed topics for sender, recipient, tx hash, and log index
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: every observed EVM log maps to exactly one canonical `EventData` payload and one confirmation policy
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: force a shallow reorg or replay around the confirmer window and check whether conflicting `EventData` rows or duplicate vote attempts appear
