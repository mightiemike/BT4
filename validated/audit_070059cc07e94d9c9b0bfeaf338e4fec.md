### Title
Panic-inducing out-of-bounds array index in `PartialOsStateDiff` decompression when parsing the OS DA output - (File: crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs)

### Summary
`PartialOsStateDiff::try_from_output_iter` calls `decompress(iter)` (defined in `crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs`) to reconstruct the data-availability state diff felts from a compressed representation embedded in the Starknet OS/program output. [1](#0-0)  This mirrors the class of bug in CVE-2017-5508, where a crafted, malformed input to a decoder writes into a fixed structure using attacker-influenced offsets/lengths without validating that the offsets stay within array bounds, crashing the process.

### Finding Description
`decompress()` unpacks header fields (`unique_value_bucket_lengths`, `n_repeating_values`, `data_len`) directly from the untrusted felt stream and uses them, together with per-element `bucket_index_per_elm` values, to walk `bucket_offset_trackers` and index into `all_values`: [2](#0-1) 

`bucket_index_per_elm` is bounded to `[0, TOTAL_N_BUCKETS)` by `unpack_chunk_to_usize`'s `elm_bound`, so the outer index `bucket_offset_trackers[bucket_index]` is protected. However, the per-bucket `offset` value stored in `bucket_offset_trackers` is only initialized from the declared (attacker-controlled) `unique_value_bucket_lengths`/`n_repeating_values` via `get_bucket_offsets`, and is incremented once per occurrence of that bucket index in `bucket_index_per_elm` — a value whose length (`data_len`) is also attacker-controlled and is not cross-validated against the sum of bucket lengths. If a crafted compressed blob declares small bucket lengths but repeats a given `bucket_index` more times than the declared bucket size (up to `data_len` occurrences), `offset` will walk past the end of that bucket's slice and, eventually, past the end of `all_values` (`all_values[*offset]`, line 612), triggering a Rust out-of-bounds panic instead of a graceful error.

### Impact Explanation
`PartialOsStateDiff::try_from_output_iter` is invoked while parsing the Starknet OS program output during OS re-execution/output validation on the sequencer/full-node side. An out-of-bounds panic here crashes the process performing OS output parsing (a `panic!`/abort rather than a `Result::Err`), which is a denial-of-service on the node re-executing or validating a block's OS output — directly analogous to the "application crash via a crafted TIFF file" described in CVE-2017-5508, where malformed structured input causes an unchecked index/read to run out of bounds.

### Likelihood Explanation
Reachability depends on how the OS program output (which embeds this compressed state diff) is produced and whether it can be attacker-influenced before reaching this decoder. In the intended flow, the compressed data is expected to be produced by the trusted Starknet OS Cairo program (which cryptographically commits to a valid compression per the `compress`/`decompress` invariant enforced in Cairo, see `crates/apollo_starknet_os_program/.../compression.cairo`). I could not fully verify within the available context whether this Rust-side `decompress` function is fed exclusively from OS-attested output that has already been separately validated to match the Cairo-side compression invariant, or whether it can be reached with attacker-supplied bytes before such validation (e.g., from a malicious/buggy prover output, or a different, less-trusted producer of the felt stream). This uncertainty materially affects likelihood and should be verified against the calling context of `PartialOsStateDiff::try_from_output_iter` and how/where the felt iterator it consumes is sourced and validated.

### Recommendation
Add explicit bounds checks before indexing: verify that every `offset` derived per bucket never exceeds the bucket's declared length (i.e., validate that `bucket_index_per_elm`'s bucket-length invariant holds, matching what the Cairo-side `decompress` enforces) and check `all_values.len()` before reading, returning a `Result::Err` (propagated up as `OsOutputError`) instead of allowing an unchecked index causing a panic. Consider using `.get(*offset)` with proper error propagation instead of direct indexing in `crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs`.

### Proof of Concept
Not confirmed end-to-end due to inability to verify the full trust boundary of the felt stream feeding `PartialOsStateDiff::try_from_output_iter` in this session; a concrete PoC would construct a compressed OS-output blob whose header declares bucket lengths smaller than the number of times the corresponding bucket index appears in `bucket_index_per_elm` (with `data_len` sized accordingly), causing `all_values[*offset]` at `crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs:612` to index past the vector's end and panic when parsed by `PartialOsStateDiff::try_from_output_iter`.

### Citations

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

**File:** crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs (L571-617)
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
}
```
