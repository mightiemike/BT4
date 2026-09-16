Found a valid analog. The `PartialOsStateDiff::try_from_output_iter` path in `crates/starknet_os/src/io/os_output_types.rs` calls `decompress` on data taken from the OS program output stream, and this Rust `decompress` implementation performs unchecked array indexing driven by attacker/prover-influenced length and index fields, unlike the Cairo `decompress` in `compression.cairo` which cryptographically enforces bucket-offset consistency via `dict_squash`.

### Title
Out-of-bounds panic in stateless-compression `decompress` due to unchecked bucket lengths/indices from state-diff output - (File: crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs)

### Summary
The Rust reference implementation of stateless-compression decompression, `decompress` in `crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs:545-617`, unpacks a header containing `unique_value_bucket_lengths`, `n_repeating_values`, `data_len`, and `bucket_index_per_elm` directly from the felt stream and then indexes `unique_values[*ptr]` and `all_values[*offset]` without any bounds validation, analogous to the missing dimension/subsampling bound checks in FFmpeg's `decode_init` (CVE-2018-7557).

### Finding Description
`decompress` (utils.rs:545) reads header fields via `unpack_chunk_to_usize` with no range checks against the actual remaining compressed data or array sizes: [1](#0-0) 

It then performs direct indexing operations that can go out of bounds if the packed values are inconsistent (e.g. a `repeating_value_pointers` entry pointing past `unique_values.len()`, or a `bucket_index_per_elm` entry ≥ `TOTAL_N_BUCKETS`, or a `bucket_offset_trackers[bucket_index]` value that overruns `all_values`): [2](#0-1) 

This function is reachable from `PartialOsStateDiff::try_from_output_iter`, which decompresses the felt stream taken from the Starknet OS program output (state diff) during OS output parsing: [3](#0-2) 

By contrast, the corresponding Cairo implementation in `compression.cairo` (used inside the STARK-provable OS execution) enforces the same invariants cryptographically via `dict_squash`/`dict_update`, so a malicious guess of offsets cannot escape unnoticed: [4](#0-3) 

The Rust `decompress` path lacks this equivalent enforcement — it is a plain, unchecked re-implementation used to parse OS output outside the proof system.

### Impact Explanation
If `decompress` is invoked on OS output that is not independently re-verified against the Cairo-enforced trace (e.g., in tooling/services that parse the program output directly, such as OS-output consumers/aggregator or state-diff decoding paths), a malformed but still felt-encodable header/index stream can trigger an out-of-bounds `Vec` index, causing a Rust panic (process abort) rather than a graceful error. This is a denial-of-service class issue matching CVE-2018-7557's "out of array read" pattern, potentially halting a node process that parses/decodes the state diff and thus contributing to a node/service being unable to process further blocks.

### Likelihood Explanation
Reachability depends on which callers feed externally/prover-supplied felt streams into `decompress` without prior Cairo-level validation. `PartialOsStateDiff::try_from_output_iter` consumes the OS output stream directly in Rust; if this parsing occurs before/independent of the STARK proof verification enforcing the packed invariants, a crafted output stream (e.g. supplied by a malicious/buggy prover, or replayed/malformed state diff bytes) can drive out-of-bounds indexing. I was not able to fully trace every caller of `try_from_output_iter`/`PartialOsStateDiff` to confirm whether all call sites always sit downstream of full proof verification — this should be verified further, as it affects whether the path is reachable purely from untrusted output without cryptographic gating.

### Recommendation
Add explicit bounds checks in `decompress` (and `unpack_chunk_to_usize`, `unpack_felts_to`) before indexing: validate `n_elms_per_felt`, bucket lengths sum, `repeating_value_pointers` entries `< unique_values.len()`, and `bucket_index_per_elm` entries `< TOTAL_N_BUCKETS`, and bound-check `bucket_offset_trackers[bucket_index]` against `all_values.len()` before use — returning a proper `Result::Err` instead of panicking on invalid input, matching the safety guarantees already enforced in the Cairo `compression.cairo` implementation.

### Proof of Concept
Construct a compressed felt stream whose packed header sets `unique_value_bucket_lengths` and `n_repeating_values` such that the derived `repeating_value_pointers` contains an index ≥ `unique_values.len()`, or whose `bucket_index_per_elm` contains a value ≥ `TOTAL_N_BUCKETS`; feeding this stream through `decompress` (utils.rs:545) — reachable via `PartialOsStateDiff::try_from_output_iter` (os_output_types.rs:401) on an OS output stream — causes an out-of-bounds `Vec` index panic at line 594 (`unique_values[*ptr]`) or line 612 (`all_values[*offset]`).

### Citations

**File:** crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs (L571-616)
```rust
    let header = unpack_chunk_to_usize(compressed, HEADER_LEN, HEADER_ELM_BOUND);
    let version = &header[0];
    assert!(version == &usize::from(COMPRESSION_VERSION), "Unsupported compression version.");

    let data_len = &header[1];
    let unique_value_bucket_lengths: Vec<usize> = header[2..2 + N_UNIQUE_BUCKETS].to_vec();
    let n_repeating_values = &header[2 + N_UNIQUE_BUCKETS];

    let mut unique_values = Vec::new();
    unique_values.extend(compressed.take(unique_value_bucket_lengths[0])); // 252 bucket.
    unique_values.extend(unpack_chunk::<125>(compressed, unique_value_bucket_lengths[1]));
    unique_values.extend(unpack_chunk::<83>(compressed, unique_value_bucket_lengths[2]));
    unique_values.extend(unpack_chunk::<62>(compressed, unique_value_bucket_lengths[3]));
    unique_values.extend(unpack_chunk::<31>(compressed, unique_value_bucket_lengths[4]));
    unique_values.extend(unpack_chunk::<15>(compressed, unique_value_bucket_lengths[5]));

    let repeating_value_pointers = unpack_chunk_to_usize(
        compressed,
        *n_repeating_values,
        unique_values.len().try_into().unwrap(),
    );

    let repeating_values: Vec<_> =
        repeating_value_pointers.iter().map(|ptr| unique_values[*ptr]).collect();

    let mut all_values = unique_values;
    all_values.extend(repeating_values);

    let bucket_index_per_elm: Vec<usize> =
        unpack_chunk_to_usize(compressed, *data_len, TOTAL_N_BUCKETS.try_into().unwrap());

    let all_bucket_lengths: Vec<usize> =
        unique_value_bucket_lengths.into_iter().chain([*n_repeating_values]).collect();

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

**File:** crates/starknet_os/src/io/os_output_types.rs (L401-420)
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
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/data_availability/compression.cairo (L286-302)
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
```
