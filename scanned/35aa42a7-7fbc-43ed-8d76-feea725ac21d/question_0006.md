# Q0006: EVM signature-data decode - topic binding partial decode

## Question
If a user submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields, can `decodeSignatureData` be pushed into a path where indexed topics for sender, recipient, tx hash, and log index causes it to accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, so that every observed EVM log maps to exactly one canonical `EventData` payload and one confirmation policy no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/evm/event_parser.go:decodeSignatureData
- Entrypoint: submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields
- Attacker controls: indexed topics for sender, recipient, tx hash, and log index
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: every observed EVM log maps to exactly one canonical `EventData` payload and one confirmation policy
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: force a shallow reorg or replay around the confirmer window and check whether conflicting `EventData` rows or duplicate vote attempts appear
