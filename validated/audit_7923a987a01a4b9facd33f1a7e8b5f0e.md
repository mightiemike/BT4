### Title
Unbounded gzip decompression of RPC-submitted class program — no size limit unlike other decompression paths - ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
`decompress_program` in `apollo_rpc` decodes and gzip-decompresses a client-supplied `base64_compressed_program` string with no output-size bound, whereas every other class-decompression path in the codebase (`starknet_api::compression_utils::decode_and_decompress_with_size_limit`, `apollo_storage::compression_utils::decompress` with `MAX_DECOMPRESSED_SIZE`) enforces an explicit cap. This is the same bug class as the vm2 report: a resource-consumption guard exists and is deliberately used elsewhere in the codebase, but one specific reachable code path bypasses it entirely, allowing a small attacker payload to force a large host memory allocation.

### Finding Description
`decompress_program` reads an arbitrary amount of decompressed bytes into an unbounded `Vec<u8>`: [1](#0-0) 

Contrast this with the two other decompression helpers in the repo, both of which cap the output size before returning: [2](#0-1) [3](#0-2) 

and the deprecated gateway class conversion path, which explicitly threads a `max_size` bound into the same kind of gzip/base64 decode used by legacy contract class deserialization: [4](#0-3) [5](#0-4) 

`decompress_program` has none of this: `decoder.read_to_end(&mut decompressed)` will happily decompress a gzip stream of arbitrary decompressed size (a "gzip bomb" — a few KB compressed can expand to gigabytes), and the `// TODO(dan): add time and size limits.` comment confirms this was a known, unaddressed gap rather than an intentional design choice.

### Impact Explanation
An attacker who can reach this code path (any client submitting a base64-gzip-encoded legacy/Cairo0 program via the affected RPC method) can force a single call to allocate an essentially unbounded amount of host memory and CPU time decompressing it, with no interruption mechanism (unlike Cairo VM step limits, which don't apply to this pre-execution RPC decoding path). Repeated calls can exhaust node memory and OOM the RPC-serving process, denying service to legitimate JSON-RPC clients. This mirrors exactly the "small payload amplified into large synchronous host allocation, bypassing the cap the rest of the codebase relies on" pattern from the referenced vm2 advisory.

### Likelihood Explanation
High: this requires no privileged access, no valid signature, and no special conditions — a single crafted JSON-RPC request with a gzip bomb embedded as a base64 string reaches `decompress_program` directly. The absence of a size limit is explicit and unconditional (no config knob to opt into protection, unlike `bufferAllocLimit` for vm2 or `MAX_DECOMPRESSED_SIZE` used elsewhere in this same repo).

### Recommendation
Route `decompress_program` through the same bounded decompression helper already used elsewhere in the codebase, e.g. `decode_and_decompress_with_size_limit` (or a `Read::take(max_size + 1)` guard as done in `starknet_api::compression_utils::decompress_with_size_limit`), enforcing a configurable maximum decompressed program size and rejecting/erroring before the full buffer is materialized.

### Proof of Concept
1. Construct a program consisting of a long run of repeated bytes (e.g., all zeros), which gzip compresses at a very high ratio.
2. Gzip-compress it and base64-encode the compressed bytes to produce a payload of a few KB.
3. Submit it as `base64_compressed_program` to the RPC endpoint that calls `decompress_program` (e.g. the legacy/Cairo0 program field of a trace/simulate/class RPC method in `apollo_rpc`).
4. Observe `decoder.read_to_end(&mut decompressed)` allocate and fill a multi-hundred-MB to multi-GB buffer from a KB-sized request, with no configured ceiling, analogous to the `Buffer.concat`/`Buffer.from({length: N})` bypass in the vm2 advisory.

**Note on uncertainty**: I was not able to fully trace, within the available tool budget, the exact external JSON-RPC method(s)/parameter names that route into `decompress_program` (only its two call sites in `apollo_rpc/src/v0_8/execution_test.rs` and its own module were found in the index). I'm confident the function itself is unbounded and reachable from RPC-submitted class data based on its signature and the surrounding `ApiContractClass`/`GatewayContractClass` conversions, but confirming the precise external RPC method name would benefit from a full-repo Devin session with complete file access, since the index used here may not include every caller.

### Citations

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

**File:** crates/apollo_storage/src/compression_utils.rs (L12-17)
```rust
// The maximum size of the decompressed data.
// TODO(Dvir): consider defining this for each type separately and pass it as an argument to the
// decompress function.
pub(crate) const MAX_DECOMPRESSED_SIZE: usize = 1 << 28; // 256 MB
// The compression level to use. Higher levels are slower but compress better.
const COMPRESSION_LEVEL: i32 = zstd::DEFAULT_COMPRESSION_LEVEL;
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

**File:** crates/apollo_protobuf/src/converters/class.rs (L131-136)
```rust
        let abi = serde_json::from_str(&value.abi)?;
        // TODO(dan): use config for this.
        const MAX_CAIRO0_PROGRAM_SIZE: usize = 4 * 1024 * 1024; // 4MB
        let program =
            decode_and_decompress_with_size_limit(&value.program, MAX_CAIRO0_PROGRAM_SIZE)?;

```
