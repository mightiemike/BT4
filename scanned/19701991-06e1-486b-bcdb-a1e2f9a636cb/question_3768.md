# Q3768: batch-repeat node degradation in RuntimeData.getRemoteAddr

## Question
Can an unprivileged attacker repeat /wallet/estimateenergy with a valid but adversarial request mix so framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr drives sustained CPU, memory, or disk pressure below true caller cost and reaches Materially underpriced public execution work or deterministic node halt?

## Target
- File/function: framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr
- Entrypoint: /wallet/estimateenergy
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Mix the most expensive valid query shapes, repeat them across public surfaces, and look for shared caches or stores that amplify work.
- Invariant to test: A public API mix must remain resource-bounded under repeated valid requests from one unprivileged user.
- Expected Immunefi impact: Materially underpriced public execution work or deterministic node halt
- Fast validation: Stress the worst valid request shapes across HTTP/gRPC/JSON-RPC and measure whether attacker cost tracks node-side work.
