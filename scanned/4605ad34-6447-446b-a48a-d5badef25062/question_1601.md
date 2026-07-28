# Q1601: EVM outbound observe - topic binding field confusion

## Question
If a user emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries, can `parseOutboundObservationEvent` be pushed into a path where indexed topics for sender, recipient, tx hash, and log index causes it to bind the right `EventID` to the wrong decoded fields so one user transaction is interpreted as a different transfer or execution, so that only a fully decoded gateway event may become a Push-chain inbound or outbound observation no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/event_parser.go:parseOutboundObservationEvent
- Entrypoint: emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries
- Attacker controls: indexed topics for sender, recipient, tx hash, and log index
- Exploit idea: bind the right `EventID` to the wrong decoded fields so one user transaction is interpreted as a different transfer or execution
- Invariant to test: only a fully decoded gateway event may become a Push-chain inbound or outbound observation
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: trace one event from listener to confirmer to processor and verify malformed logs cannot move from `PENDING` to `CONFIRMED` or `COMPLETED`
