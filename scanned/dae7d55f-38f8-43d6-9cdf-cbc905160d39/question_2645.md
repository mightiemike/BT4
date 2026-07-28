# Q2645: EVM confirm selection - ordering/finality partial decode

## Question
When an unprivileged actor emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries, does `getRequiredConfirmations` remain safe if they control log ordering across adjacent blocks plus the exact reorg and confirmation timing, or can that make it accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, violate the rule that only a fully decoded gateway event may become a Push-chain inbound or outbound observation, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/event_confirmer.go:getRequiredConfirmations
- Entrypoint: emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries
- Attacker controls: log ordering across adjacent blocks plus the exact reorg and confirmation timing
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: only a fully decoded gateway event may become a Push-chain inbound or outbound observation
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: trace one event from listener to confirmer to processor and verify malformed logs cannot move from `PENDING` to `CONFIRMED` or `COMPLETED`
