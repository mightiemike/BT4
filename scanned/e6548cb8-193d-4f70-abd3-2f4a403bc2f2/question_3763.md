# Q3763: EVM outbound observe - value fields partial decode

## Question
When an unprivileged actor repeat or reorder public EVM gateway transactions around a reorg or confirmation boundary that any normal user can reach, does `parseOutboundObservationEvent` remain safe if they control token, amount, revert recipient, tx type, and `fromCEA` bits encoded in the event payload, or can that make it accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, violate the rule that every observed EVM log maps to exactly one canonical `EventData` payload and one confirmation policy, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/event_parser.go:parseOutboundObservationEvent
- Entrypoint: repeat or reorder public EVM gateway transactions around a reorg or confirmation boundary that any normal user can reach
- Attacker controls: token, amount, revert recipient, tx type, and `fromCEA` bits encoded in the event payload
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: every observed EVM log maps to exactly one canonical `EventData` payload and one confirmation policy
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force a shallow reorg or replay around the confirmer window and check whether conflicting `EventData` rows or duplicate vote attempts appear
