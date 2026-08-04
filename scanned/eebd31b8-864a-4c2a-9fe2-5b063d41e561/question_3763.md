# Q3763: retry-broadcast race in RuntimeData.getRemoteAddr

## Question
Can an unprivileged attacker use public retries around /wallet/estimateenergy so framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr accepts the same logical request from multiple surfaces or timing windows, causing Repeatable invalid settlement from one logical execution?

## Target
- File/function: framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr
- Entrypoint: /wallet/estimateenergy
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Probe raw/built transaction retries, mixed hex and JSON forms, and closely spaced repeats across HTTP, gRPC, and JSON-RPC.
- Invariant to test: Public retries must preserve one-time semantics and converge on one settlement outcome regardless of surface or serialization.
- Expected Immunefi impact: Repeatable invalid settlement from one logical execution
- Fast validation: Race the same payload via all public broadcast surfaces and assert pending, recent, and final state converge to one settlement.
