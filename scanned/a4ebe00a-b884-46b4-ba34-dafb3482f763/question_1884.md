# Q1884: EVM universal-tx decode - abi offsets partial decode

## Question
When an unprivileged actor emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries, does `parseUniversalTx` remain safe if they control dynamic ABI offsets for payload bytes and signature data inside log data, or can that make it accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, violate the rule that every observed EVM log maps to exactly one canonical `EventData` payload and one confirmation policy, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/event_parser.go:parseUniversalTx
- Entrypoint: emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries
- Attacker controls: dynamic ABI offsets for payload bytes and signature data inside log data
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: every observed EVM log maps to exactly one canonical `EventData` payload and one confirmation policy
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: force a shallow reorg or replay around the confirmer window and check whether conflicting `EventData` rows or duplicate vote attempts appear
