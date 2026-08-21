# Q343: JsonRpcApiUtil: argument coercion mismatch

## Question
Can an unprivileged attacker (JSON-RPC endpoint) abuse `JsonRpcApiUtil.triggerCallContract` in `framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java` — where the attacker sends a JSON-RPC param to JsonRpcApiUtil.triggerCallContract whose hex/quantity coercion differs from the value later used for state or gas, creating a semantic gap — to break the invariant that coerced argument equals the value used downstream in execution, leading to: Consensus divergence (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java` -> `JsonRpcApiUtil.triggerCallContract`
- Entrypoint: JSON-RPC call to JsonRpcApiUtil.triggerCallContract with ambiguous quantity encoding
- Attacker controls: request/transaction/contract inputs to `JsonRpcApiUtil.triggerCallContract` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a JSON-RPC param to JsonRpcApiUtil.triggerCallContract whose hex/quantity coercion differs from the value later used for state or gas, creating a semantic gap
- Invariant to test: coerced argument equals the value used downstream in execution
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential test odd-length hex / leading-zero quantities
