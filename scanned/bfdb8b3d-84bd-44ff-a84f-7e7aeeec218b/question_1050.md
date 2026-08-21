# Q1050: JsonRpcServlet: argument coercion mismatch

## Question
Can an unprivileged attacker (JSON-RPC endpoint) abuse `JsonRpcServlet.init` in `framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java` — where the attacker sends a JSON-RPC param to JsonRpcServlet.init whose hex/quantity coercion differs from the value later used for state or gas, creating a semantic gap — to break the invariant that coerced argument equals the value used downstream in execution, leading to: Consensus divergence (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java` -> `JsonRpcServlet.init`
- Entrypoint: JSON-RPC call to JsonRpcServlet.init with ambiguous quantity encoding
- Attacker controls: request/transaction/contract inputs to `JsonRpcServlet.init` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a JSON-RPC param to JsonRpcServlet.init whose hex/quantity coercion differs from the value later used for state or gas, creating a semantic gap
- Invariant to test: coerced argument equals the value used downstream in execution
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential test odd-length hex / leading-zero quantities
