# Q1356: Coordinator sign setup - deadline/expiry recovered double-sign

## Question
If a user submit many public Push-chain actions that create concurrent outbounds to the same destination chain, can `createSignSetup` be pushed into a path where signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast causes it to recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states, so that session-time verification must reconstruct the same transaction semantics the coordinator selected earlier no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:createSignSetup
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast
- Exploit idea: recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states
- Invariant to test: session-time verification must reconstruct the same transaction semantics the coordinator selected earlier
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: enqueue several outbounds with controlled deadlines and payload sizes, then inspect nonce assignment, eventstore rows, and signing order under load
