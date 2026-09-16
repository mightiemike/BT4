### Title
Missing bounds validation in Rust `decompress` allows out-of-bounds panic when parsing an attacker-influenced DA state-diff segment - (File: crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs)

### Summary
The CVE describes a buffer over-read in `tif_zip.c`'s decode/encode path caused by insufficient validation of length/index fields taken from an untrusted, crafted input (a BMP image). The closest analog in this repo is the Rust-side stateless-compression `decompress` function used to parse the L2 state-diff data-availability (DA) segment. Unlike its Cairo counterpart, which cryptographically enforces that every bucket index and repeating-value pointer is in-range via `dict_squash`/`dict_update`, the Rust re-implementation performs the same unpacking with **no bounds checks** on attacker-controllable index/pointer fields before using them to index into vectors.

### Finding Description
`decompress` in `crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs:543-617` reconstructs the DA segment from a compressed stream of `Felt`s:

- `repeating_value_pointers` is unpacked from the compressed stream and used directly to index into `unique_values`: [1](#0-0) 
  There is no check that each pointer is `< unique_values.len()`.

- `bucket_index_per_elm` is unpacked from the compressed stream and used directly to index into `bucket_offset_trackers` (`Vec` of length `TOTAL_N_BUCKETS`): [2](#0-1) 
  There is no check that each `bucket_index` is `< TOTAL_N_BUCKETS`, and no check that `offset < all_values.len()` before `all_values[*offset]` is read.

Compare this to the Cairo implementation in `crates/apollo_starknet_os_program/.../compression.cairo`, which explicitly documents and enforces these invariants using a squashed dictionary of bucket offsets so that any out-of-range index/pointer is provably rejected before the Cairo run is accepted: [3](#0-2) 

The Rust `decompress` function is reached whenever a `PartialOsStateDiff` (or similarly compressed `OsStateDiff` variant) is parsed from an output/DA iterator: [4](#0-3) 
and this path is also used to decrypt/parse a DA segment reconstructed from L1 blobs: [5](#0-4) 

If any node-side component parses a compressed DA segment/blob using this Rust path (i.e., without first re-running the Cairo OS proof verification that enforces the bounds), a crafted compressed segment containing an out-of-range bucket index or repeating-value pointer will cause `Vec` indexing to panic with an out-of-bounds error rather than returning a decode error.

### Impact Explanation
A panic in this parsing path is a process-level crash (Rust panics on out-of-bounds slice/vector indexing abort the calling thread/process by default). If this parsing code executes on a path that consumes externally-supplied bytes (DA blobs read from L1, or state-diff segments obtained before/independently of full OS-proof verification), a single crafted, un-verified compressed payload can crash the consuming component, resulting in denial of service for nodes attempting to reconstruct state from that DA segment. This matches the "network unable to confirm new transactions" / node-divergence class of impact when such parsing runs unguarded on the hot path of block/state processing.

### Likelihood Explanation
I could not fully confirm, within the available tooling, that this Rust `decompress`/`try_from_output_iter` path is invoked on fully untrusted data *prior to* Cairo-OS proof verification (which would otherwise make any invalid bucket index/pointer a proof-rejection rather than a runtime panic). The clear callers found (`state_diff_encryption`, `os_output_types`, and test/aggregator code) suggest usage in offline/aux tooling (blob decryption/reconstruction, aggregator, tests) rather than proven in-circuit execution — but confirming whether any of these call sites run on unverified attacker input reachable by an unprivileged L1 sender/contract deployer requires deeper tracing that exceeded the available search budget.

### Recommendation
Add explicit bounds checks in the Rust `decompress` implementation mirroring the Cairo guarantees: validate that every `repeating_value_pointers` entry is `< unique_values.len()` and every `bucket_index_per_elm` entry is `< TOTAL_N_BUCKETS`/`bucket_offset_trackers.len()`, returning a decode error (e.g., a new `OsHintError`/`OsOutputError` variant) instead of allowing a panic on out-of-range indices. This should be done regardless of whether the current call sites are provably reachable by untrusted input, since this function's only defense against malformed input today is implicit in unverified Rust code.

### Proof of Concept
Not fully verifiable without confirming the exact untrusted-input call site; conceptually: construct a compressed `Vec<Felt>` whose header claims a small number of unique values (e.g., 1) but whose `bucket_index_per_elm` or `repeating_value_pointers` section encodes an index equal to or greater than the corresponding vector length, then call `decompress(&mut compressed.into_iter())` — this panics with an index-out-of-bounds error rather than returning an `Err`.

### Citations

**File:** crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs (L587-594)
```rust
    let repeating_value_pointers = unpack_chunk_to_usize(
        compressed,
        *n_repeating_values,
        unique_values.len().try_into().unwrap(),
    );

    let repeating_values: Vec<_> =
        repeating_value_pointers.iter().map(|ptr| unique_values[*ptr]).collect();
```

**File:** crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs (L605-616)
```rust
    let bucket_offsets = get_bucket_offsets(&all_bucket_lengths);

    let mut bucket_offset_trackers: Vec<_> = bucket_offsets;

    let mut result = Vec::new();
    for bucket_index in bucket_index_per_elm {
        let offset = &mut bucket_offset_trackers[bucket_index];
        let value = all_values[*offset];
        *offset += 1;
        result.push(value);
    }
    result
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/data_availability/compression.cairo (L286-303)
```text
        // Verify there was no out-of-bound access to `all_values` array by checking the bucket
        // offset final values.
        dict_update(key=0, prev_value=bucket1_offset, new_value=bucket1_offset);
        dict_update(key=1, prev_value=bucket2_offset, new_value=bucket2_offset);
        dict_update(key=2, prev_value=bucket3_offset, new_value=bucket3_offset);
        dict_update(key=3, prev_value=bucket4_offset, new_value=bucket4_offset);
        dict_update(key=4, prev_value=bucket5_offset, new_value=bucket5_offset);
        dict_update(key=5, prev_value=bucket6_offset, new_value=bucket6_offset);
        tempvar all_values_len = bucket6_offset + header.n_repeating_values;
        dict_update(key=6, prev_value=all_values_len, new_value=all_values_len);
    }
    // Verify the dict reads by squashing the updates.
    // Note that there is no need to verify the initial values:
    // the dict keys are contained in [0, 1, ... TOTAL_N_BUCKETS - 1] since `unpack_pointers`
    // guarantees that each pointer is in this range, and they were all set explicitly above.
    dict_squash(dict_accesses_start=dict_ptr_start, dict_accesses_end=dict_ptr);
    return ();
}
```

**File:** crates/starknet_os/src/io/os_output_types.rs (L401-419)
```rust
impl TryFromOutputIter for PartialOsStateDiff {
    fn try_from_output_iter<It: Iterator<Item = Felt>>(
        iter: &mut It,
        private_keys: Option<&Vec<Felt>>,
    ) -> Result<Self, OsOutputError> {
        let iter = &mut maybe_decrypt_iter(iter, private_keys);
        let decompressed = &mut decompress(iter).into_iter().chain(iter);

        Ok(Self {
            contracts: Vec::<PartialContractChanges>::try_from_output_iter(
                decompressed,
                private_keys,
            )?,
            classes: Vec::<PartialCompiledClassHashUpdate>::try_from_output_iter(
                decompressed,
                private_keys,
            )?,
        })
    }
```

**File:** crates/starknet_os/src/hints/hint_implementation/state_diff_encryption/utils.rs (L212-236)
```rust
// TODO(Einat): Test this function in the OS tests.
#[allow(dead_code)]
pub fn decrypt_state_diff_from_blobs(
    blobs: Vec<[u8; BYTES_PER_BLOB]>,
    private_key: Felt,
    committee_index: usize,
) -> Result<PartialOsStateDiff, DecryptionError> {
    let decoded_blobs = decode_blobs(blobs)?;

    let n_keys: usize = decoded_blobs[0].try_into().expect("n_keys should fit in usize");
    let sn_public_key = decoded_blobs[committee_index + 1];
    let encrypted_symmetric_key = decoded_blobs[n_keys + committee_index + 1];

    // Decrypt the state diff (may include trailing zeros from blob padding).
    let decrypted_da = decrypt_state_diff(
        private_key,
        sn_public_key,
        encrypted_symmetric_key,
        &decoded_blobs[2 * n_keys + 1..],
    );

    // The parser will consume only what it needs and ignore trailing padding.
    // No need to pass private keys here, as the DA segment is already decrypted.
    Ok(PartialOsStateDiff::try_from_output_iter(&mut decrypted_da.into_iter(), None)?)
}
```
