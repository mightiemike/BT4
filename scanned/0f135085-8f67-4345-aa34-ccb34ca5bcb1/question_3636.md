# Q3636: batch-repeat node degradation in MetricInterceptor.preHandleJson

## Question
Can an unprivileged attacker repeat /jsonrpc with a valid but adversarial request mix so framework/src/main/java/org/tron/core/services/jsonrpc/interceptor/MetricInterceptor.java::preHandleJson drives sustained CPU, memory, or disk pressure below true caller cost and reaches Materially underpriced CPU, memory, disk, or state-iteration work on a public API path?

## Target
- File/function: framework/src/main/java/org/tron/core/services/jsonrpc/interceptor/MetricInterceptor.java::preHandleJson
- Entrypoint: /jsonrpc
- Attacker controls: RPC params, block tags and ranges, topic arrays, filter ids, raw hex, pagination, and visible/base58/hex encoding
- Exploit idea: Mix the most expensive valid query shapes, repeat them across public surfaces, and look for shared caches or stores that amplify work.
- Invariant to test: A public API mix must remain resource-bounded under repeated valid requests from one unprivileged user.
- Expected Immunefi impact: Materially underpriced CPU, memory, disk, or state-iteration work on a public API path
- Fast validation: Stress the worst valid request shapes across HTTP/gRPC/JSON-RPC and measure whether attacker cost tracks node-side work.
