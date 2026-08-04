# Q3475: retry-broadcast race in FullNodeJsonRpcHttpService.addServlet

## Question
Can an unprivileged attacker use public retries around /jsonrpc so framework/src/main/java/org/tron/core/services/jsonrpc/FullNodeJsonRpcHttpService.java::addServlet accepts the same logical request from multiple surfaces or timing windows, causing Duplicate execution or stale-state reuse through public API retries?

## Target
- File/function: framework/src/main/java/org/tron/core/services/jsonrpc/FullNodeJsonRpcHttpService.java::addServlet
- Entrypoint: /jsonrpc
- Attacker controls: RPC params, block tags and ranges, topic arrays, filter ids, raw hex, pagination, and visible/base58/hex encoding
- Exploit idea: Probe raw/built transaction retries, mixed hex and JSON forms, and closely spaced repeats across HTTP, gRPC, and JSON-RPC.
- Invariant to test: Public retries must preserve one-time semantics and converge on one settlement outcome regardless of surface or serialization.
- Expected Immunefi impact: Duplicate execution or stale-state reuse through public API retries
- Fast validation: Race the same payload via all public broadcast surfaces and assert pending, recent, and final state converge to one settlement.
