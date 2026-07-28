# Q2266: EVM resume height - value fields partial decode

## Question
When an unprivileged actor emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries, does `getStartBlock` remain safe if they control token, amount, revert recipient, tx type, and `fromCEA` bits encoded in the event payload, or can that make it accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, violate the rule that reorged, malformed, or wrong-method EVM logs never reach `StatusCompleted`, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/event_listener.go:getStartBlock
- Entrypoint: emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries
- Attacker controls: token, amount, revert recipient, tx type, and `fromCEA` bits encoded in the event payload
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: reorged, malformed, or wrong-method EVM logs never reach `StatusCompleted`
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: emit crafted gateway logs on a fork or local EVM devnet and compare raw log bytes against the persisted `store.Event` row and the resulting vote message
