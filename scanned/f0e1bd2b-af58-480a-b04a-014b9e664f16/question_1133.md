# Q1133: EVM payload binding - ordering/finality partial decode

## Question
When an unprivileged actor submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields, does `decodePayload` remain safe if they control log ordering across adjacent blocks plus the exact reorg and confirmation timing, or can that make it accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, violate the rule that the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/event_parser.go:decodePayload
- Entrypoint: submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields
- Attacker controls: log ordering across adjacent blocks plus the exact reorg and confirmation timing
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: mutate offsets, topic counts, and trailing words, then diff parsed `EventData` against the original ABI payload before and after `constructInbound`
