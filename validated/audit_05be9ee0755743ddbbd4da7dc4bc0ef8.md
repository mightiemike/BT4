### Title
Unbounded hex payload in `web3_sha3` JSON-RPC method enables memory/CPU exhaustion DoS - ([File: framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java])

### Summary
The JSON-RPC method `web3Sha3` decodes an attacker-supplied hex string with `ByteArray.fromHexString` and feeds the resulting byte array to `Hash.sha3`, without any length validation before decoding or hashing. An unprivileged remote client can submit very large hex payloads (repeated concurrently) to force large memory allocations and CPU-bound `MessageDigest.update` calls on JSON-RPC worker threads.

### Finding Description
`TronJsonRpcImpl.web3Sha3(String)` is exposed as a public unauthenticated JSON-RPC endpoint. It takes the client-supplied string, strips `0x`, and calls `ByteArray.fromHexString(...)`, then passes the resulting byte array directly into `Hash.sha3(byte[])`, which performs a Keccak-256 `MessageDigest.update` over the whole buffer. There is no size cap on the input string or resulting byte array anywhere on this path — unlike transaction-broadcast paths, which are bounded by protobuf message size limits, bandwidth/energy accounting, and `TransactionCapsule` validation, the JSON-RPC `web3_sha3` endpoint has no equivalent gate. I attempted to confirm whether the JSON-RPC HTTP server layer (Netty pipeline) enforces a max content-length via `HttpObjectAggregator` or similar, but no such configuration was found in the repository; I could not conclusively rule out a container-level limit outside indexed code, so this should be verified directly against the running server configuration.

Because the endpoint requires no authentication, any anonymous client can open many concurrent JSON-RPC connections and send oversized `web3_sha3` requests, each of which allocates a large byte array and performs a proportionally large hashing operation on a worker thread from the shared JSON-RPC executor pool, degrading availability for legitimate requests.

### Impact Explanation
This is a Denial-of-Service vector against the RPC-API layer (not consensus or asset safety): it can cause elevated CPU/memory usage and thread-pool starvation on the node, potentially causing the JSON-RPC service (and possibly the whole node if under memory pressure) to become unresponsive to legitimate clients. This matches the "DoS via RPC-API" bounty class rather than a consensus or fund-safety issue.

### Likelihood Explanation
Preconditions are only: JSON-RPC endpoint enabled (a supported node configuration, not exotic), and no attacker privilege is required — this is a plain unauthenticated HTTP POST. The attacker's cost is negligible (bandwidth to send the payload); repeatability is trivial by opening multiple connections. The main uncertainty is whether an external reverse proxy or Netty content-length limit already mitigates this in default deployments — I could not verify presence or absence of such a limit in the indexed codebase.

### Recommendation
Add an explicit maximum length check on the input hex string in `web3Sha3` (and any other JSON-RPC methods that decode arbitrary client-supplied hex/byte data) before calling `ByteArray.fromHexString`/`Hash.sha3`, rejecting oversized requests with an RPC error. Additionally, confirm/enforce a request body size limit at the HTTP/Netty layer for the JSON-RPC server.

### Proof of Concept
```
POST /jsonrpc HTTP/1.1
Content-Type: application/json

{"jsonrpc":"2.0","method":"web3_sha3","params":["0x<200MB of hex chars>"],"id":1}
```
Expected today: server allocates the full decoded byte array and computes SHA3 over it, consuming large memory/CPU per request; repeated concurrently across many connections exhausts JSON-RPC worker threads/heap.
Expected after fix: request is rejected early with a size-limit error before any decoding/hashing occurs. [1](#0-0)

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L1-1)
```java
package org.tron.core.services.jsonrpc;
```
