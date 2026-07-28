# Q2642: EVM resume height - ordering/finality partial decode

## Question
If a user emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries, can `getStartBlock` be pushed into a path where log ordering across adjacent blocks plus the exact reorg and confirmation timing causes it to accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, so that only a fully decoded gateway event may become a Push-chain inbound or outbound observation no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/event_listener.go:getStartBlock
- Entrypoint: emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries
- Attacker controls: log ordering across adjacent blocks plus the exact reorg and confirmation timing
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: only a fully decoded gateway event may become a Push-chain inbound or outbound observation
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: trace one event from listener to confirmer to processor and verify malformed logs cannot move from `PENDING` to `CONFIRMED` or `COMPLETED`
