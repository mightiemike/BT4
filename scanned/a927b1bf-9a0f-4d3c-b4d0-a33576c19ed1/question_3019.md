# Q3019: EVM height checkpoint - topic binding partial decode

## Question
When an unprivileged actor repeat or reorder public EVM gateway transactions around a reorg or confirmation boundary that any normal user can reach, does `updateLastProcessedBlock` remain safe if they control indexed topics for sender, recipient, tx hash, and log index, or can that make it accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, violate the rule that only a fully decoded gateway event may become a Push-chain inbound or outbound observation, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/event_listener.go:updateLastProcessedBlock
- Entrypoint: repeat or reorder public EVM gateway transactions around a reorg or confirmation boundary that any normal user can reach
- Attacker controls: indexed topics for sender, recipient, tx hash, and log index
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: only a fully decoded gateway event may become a Push-chain inbound or outbound observation
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: trace one event from listener to confirmer to processor and verify malformed logs cannot move from `PENDING` to `CONFIRMED` or `COMPLETED`
