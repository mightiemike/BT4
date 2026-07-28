# Q3389: EVM payload binding - abi offsets partial decode

## Question
When an unprivileged actor repeat or reorder public EVM gateway transactions around a reorg or confirmation boundary that any normal user can reach, does `decodePayload` remain safe if they control dynamic ABI offsets for payload bytes and signature data inside log data, or can that make it accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, violate the rule that the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/event_parser.go:decodePayload
- Entrypoint: repeat or reorder public EVM gateway transactions around a reorg or confirmation boundary that any normal user can reach
- Attacker controls: dynamic ABI offsets for payload bytes and signature data inside log data
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: mutate offsets, topic counts, and trailing words, then diff parsed `EventData` against the original ABI payload before and after `constructInbound`
