# Q3010: EVM sendFunds ingest - topic binding partial decode

## Question
If a user repeat or reorder public EVM gateway transactions around a reorg or confirmation boundary that any normal user can reach, can `parseSendFundsEvent` be pushed into a path where indexed topics for sender, recipient, tx hash, and log index causes it to accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, so that only a fully decoded gateway event may become a Push-chain inbound or outbound observation no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/event_parser.go:parseSendFundsEvent
- Entrypoint: repeat or reorder public EVM gateway transactions around a reorg or confirmation boundary that any normal user can reach
- Attacker controls: indexed topics for sender, recipient, tx hash, and log index
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: only a fully decoded gateway event may become a Push-chain inbound or outbound observation
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: trace one event from listener to confirmer to processor and verify malformed logs cannot move from `PENDING` to `CONFIRMED` or `COMPLETED`
