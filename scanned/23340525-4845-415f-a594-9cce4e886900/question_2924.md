# Q2924: LogMatch: argument coercion mismatch

## Question
Can an unprivileged attacker (JSON-RPC endpoint) abuse `LogMatch.matchBlockOneByOne` in `framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogMatch.java` — where the attacker sends a JSON-RPC param to LogMatch.matchBlockOneByOne whose hex/quantity coercion differs from the value later used for state or gas, creating a semantic gap — to break the invariant that coerced argument equals the value used downstream in execution, leading to: Consensus divergence (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogMatch.java` -> `LogMatch.matchBlockOneByOne`
- Entrypoint: JSON-RPC call to LogMatch.matchBlockOneByOne with ambiguous quantity encoding
- Attacker controls: request/transaction/contract inputs to `LogMatch.matchBlockOneByOne` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a JSON-RPC param to LogMatch.matchBlockOneByOne whose hex/quantity coercion differs from the value later used for state or gas, creating a semantic gap
- Invariant to test: coerced argument equals the value used downstream in execution
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential test odd-length hex / leading-zero quantities
