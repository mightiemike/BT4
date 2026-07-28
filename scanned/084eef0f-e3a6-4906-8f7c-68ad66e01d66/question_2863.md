# Q2863: Coordinator in-flight count - deadline/expiry recovered double-sign

## Question
Can an unprivileged attacker create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls and use control over signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast so that `getInFlightSignCountPerChain` recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states, breaking the invariant that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:getInFlightSignCountPerChain
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast
- Exploit idea: recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: compare coordinator-built signing requests with sessionmanager verification output for the same outbound under edge-case fields
