### Title
Unbounded gzip decompression of RPC-submitted program data enables memory-exhaustion DoS - ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
`decompress_program` in `apollo_rpc` decodes a base64+gzip-compressed program string with **no size or time limit** before parsing it as JSON, unlike the analogous gateway path (`decode_and_decompress_with_size_limit`) which enforces a bounded decompression via `.take(max_size + 1)`. A single crafted request containing a small, highly-compressed ("gzip bomb") payload can force the node to allocate unbounded memory / CPU while decompressing, causing an out-of-memory crash or prolonged hang — the same class of impact ("hang or frequently repeatable crash / complete DOS") described in CVE-2026-34270 for MySQL's Group Replication Plugin, just triggered here via a resource-exhaustion path instead of a protocol parsing bug.

### Finding Description
The gateway's transaction-submission path is careful about decompression: `decode_and_decompress_with_size_limit` in `crates/starknet_api/src/compression_utils.rs` bounds the number of decompressed bytes read via `decompressor.take((max_size + 1)...)`, returning `CompressionError::SizeLimitExceeded` if exceeded: [1](#0-0) 

That bounded helper is used consistently for user-submitted Sierra programs, e.g. in the deprecated-gateway transaction conversion path: [2](#0-1) 

However, `apollo_rpc`'s `decompress_program` — used to decode a base64+gzip-compressed Cairo program supplied in RPC call parameters (e.g., for trace/simulation/fee-estimation flows that accept a `compiled_contract_class`/program payload) — has no such bound, explicitly marked with a TODO acknowledging the missing limits: [3](#0-2) 

`decoder.read_to_end(&mut decompressed, ...)` will keep expanding the output buffer as long as the gzip stream produces bytes, regardless of the tiny size of the compressed input. A malicious client can submit a small compressed blob (a classic "gzip bomb") that decompresses to gigabytes of data, causing the node process to allocate excessive memory and/or spend excessive CPU, which can crash the process (OOM kill) or make it unresponsive to other RPC/API requests running in the same process — directly analogous to the "hang or frequently repeatable crash" impact in the reported MySQL CVE.

### Impact Explanation
A successful attack causes memory exhaustion or CPU-bound hang in the node process serving RPC requests, which can crash or freeze the affected sequencer/RPC node. If this endpoint shares a process/runtime with other sequencer duties (e.g., in a monolithic deployment), an OOM crash can bring down block-production or transaction-serving capability, constituting a "network unable to confirm new transactions" scenario for the affected node. This matches Medium severity DoS criteria (no funds loss, but availability impact via crash/hang), consistent with the CVSS 3.1 vector in the reference report (`C:N/I:N/A:H`).

### Likelihood Explanation
Likelihood is high for triggering the underlying bug: any unauthenticated/unprivileged RPC client that can call the endpoint accepting a compressed program string can reach this code path with no authorization requirement, and a gzip bomb payload is trivial and cheap to construct. The only mitigating factor is that this is gated behind whatever request-size limits exist at the HTTP/RPC transport layer for the JSON-RPC body itself (not evaluated here — this is a lower layer than the gateway's specific `max_request_body_size`/`max_sierra_program_size` protections, which explicitly do not apply to this RPC-only code path).

### Recommendation
Apply the same bounded-decompression discipline used elsewhere in the codebase: replace the raw `GzDecoder::new(...).read_to_end(...)` call in `decompress_program` with a size-limited read (e.g. via `.take(max_size)` as done in `decompress_with_size_limit`), returning an error instead of continuing to decompress once a configured maximum program size is exceeded. Consider also bounding decompression time/CPU.

### Proof of Concept
1. Construct a program JSON payload consisting of highly repetitive data (e.g., a large array of identical values) and gzip-compress it; even a multi-GB decompressed payload can compress to a few KB.
2. Base64-encode the compressed bytes.
3. Submit an RPC request to an endpoint that accepts a compressed program (e.g., simulate/estimate-fee flows invoking `decompress_program`) with this base64 string as the program field.
4. Observe the node's memory usage grow unbounded during `decoder.read_to_end(&mut decompressed)` in `decompress_program`, until the process is OOM-killed or becomes unresponsive — with no `SizeLimitExceeded`-style rejection, unlike the equivalent gateway compression path.

Note: I was unable to fully trace which specific RPC method(s) invoke `decompress_program` (only its definition and test references were found in the index); a Devin session with full codebase access should confirm the exact caller(s) (e.g., `simulate_transactions`/`estimate_fee`/trace endpoints in `execution.rs`) to precisely scope which RPC calls are exploitable.

### Citations

**File:** crates/starknet_api/src/compression_utils.rs (L32-46)
```rust
/// Decompresses the provided data with size limits.
fn decompress_with_size_limit(
    decoded_data: Vec<u8>,
    max_size: usize,
) -> Result<Vec<u8>, CompressionError> {
    let decompressor = flate2::read::GzDecoder::new(&decoded_data[..]);
    let mut decompressed_data = Vec::new();
    decompressor
        .take((max_size + 1).try_into().expect("max_size should be less than usize::MAX"))
        .read_to_end(&mut decompressed_data)?;
    if decompressed_data.len() > max_size {
        return Err(CompressionError::SizeLimitExceeded { limit: max_size });
    }
    Ok(decompressed_data)
}
```

**File:** crates/apollo_http_server/src/deprecated_gateway_transaction.rs (L289-301)
```rust
impl DeprecatedGatewaySierraContractClass {
    pub fn convert_to_sierra_contract_class(
        self,
        max_size: usize,
    ) -> Result<SierraContractClass, CompressionError> {
        let sierra_program = decode_and_decompress_with_size_limit(&self.sierra_program, max_size)?;
        Ok(SierraContractClass {
            sierra_program,
            contract_class_version: self.contract_class_version,
            entry_points_by_type: self.entry_points_by_type,
            abi: self.abi.clone(),
        })
    }
```

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L671-682)
```rust
pub(crate) fn decompress_program(
    base64_compressed_program: &String,
) -> Result<Program, ErrorObjectOwned> {
    base64::decode(base64_compressed_program).map_err(internal_server_error)?;
    let compressed_data =
        base64::decode(base64_compressed_program).map_err(internal_server_error)?;
    // TODO(dan): add time and size limits.
    let mut decoder = GzDecoder::new(compressed_data.as_slice());
    let mut decompressed = Vec::new();
    decoder.read_to_end(&mut decompressed).map_err(internal_server_error)?;
    serde_json::from_reader(decompressed.as_slice()).map_err(internal_server_error)
}
```
