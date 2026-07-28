# Q2079: EVM height checkpoint - abi offsets early confirm

## Question
When an unprivileged actor emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries, does `updateLastProcessedBlock` remain safe if they control dynamic ABI offsets for payload bytes and signature data inside log data, or can that make it misclassify the event kind or confirmation class so a low-finality or wrong-method event reaches voting too early, violate the rule that only a fully decoded gateway event may become a Push-chain inbound or outbound observation, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/event_listener.go:updateLastProcessedBlock
- Entrypoint: emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries
- Attacker controls: dynamic ABI offsets for payload bytes and signature data inside log data
- Exploit idea: misclassify the event kind or confirmation class so a low-finality or wrong-method event reaches voting too early
- Invariant to test: only a fully decoded gateway event may become a Push-chain inbound or outbound observation
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: trace one event from listener to confirmer to processor and verify malformed logs cannot move from `PENDING` to `CONFIRMED` or `COMPLETED`
