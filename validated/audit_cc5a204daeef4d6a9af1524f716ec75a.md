## Title
Out-of-Bounds Panic in Rust `decompress()` State-Diff Parser Can Crash Starknet OS Re-Execution — (File: `crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs`)

### Summary
The Cairo-VM in-circuit `decompress` function used to reconstruct a contract's state diff is mirrored by an un-proven, un-guarded Rust helper, `decompress()`, in [1](#0-0) . Unlike the Cairo implementation, which explicitly range-checks every decoded element and proves index-correctness via `dict_update`/`dict_squash` (see `assert [range_check_ptr] = current;` and `dict_squash` in [2](#0-1)  and [3](#0-2) ), the Rust version performs raw slice indexing with no bounds validation. This is directly analogous to the rdesktop `process_secondary_order()` OOB read: a length/offset value decoded from attacker-influenced data is used to index a buffer without checking it against the buffer's actual size, producing an out-of-bounds access that aborts the process (Rust panic ≈ segfault DoS).

### Finding Description
In `decompress()`:
- The header (`version`, `data_len`, six `unique_value_bucket_lengths`, `n_repeating_values`) is decoded via `unpack_chunk_to_usize` and then indexed with a hard slice `header[2..2 + N_UNIQUE_BUCKETS]` [4](#0-3) .
- `repeating_value_pointers` are decoded and immediately used to index `unique_values[*ptr]` [5](#0-4) .
- `bucket_index_per_elm` values are used directly as indices into `bucket_offset_trackers[bucket_index]` and `all_values[*offset]` in the final reconstruction loop [6](#0-5) .

None of these indices are validated against the actual lengths of `header`, `unique_values`, `all_values`, or `bucket_offset_trackers` before use. If the supplied `compressed` iterator is shorter than the header claims (truncated/malformed DA data), or if `bucket_index_per_elm`/`repeating_value_pointers` values exceed the real array bounds (e.g., due to a header-field overflow — each header field is only `HEADER_ELM_N_BITS = 20` bits wide per [7](#0-6) , so any bucket exceeding ~2^20 elements silently wraps), the subsequent slice/vector index panics with an out-of-bounds error.

This function is exercised on the DA segment reconstructed from L1 KZG blob data during Starknet OS re-execution/state-diff decoding flows, e.g. `PartialOsStateDiff::try_from_output_iter` → `decompress(iter)` [8](#0-7) , and in the `starknet_os_flow_tests` re-execution harness's DA-segment decompression path [9](#0-8) .

### Impact Explanation
A panic in this parser during Starknet OS re-execution aborts the process performing the re-execution/verification of a block's state diff. Because this logic is on the state-diff decoding/commitment path (not gated by anything client-specific), a crafted or corrupted DA segment reaching this code causes an unhandled panic rather than a graceful error, which is a denial-of-service on the node performing re-execution — mirroring the "Denial of Service (segfault)" impact of the original CVE.

### Likelihood Explanation
Reaching this exact panic requires the header/pointer fields to be inconsistent with the actual compressed payload length (truncated data) or to overflow the 20-bit header fields (extremely large per-bucket element counts, theoretically reachable if a block's state diff produces more than ~2^20 elements in one bucket). This is a narrower trigger condition than the fully generic parsing path in rdesktop, so likelihood is moderate rather than trivial — it depends on whether bucket sizes can practically exceed the 20-bit header capacity within a single block's resource limits, which was not independently verified in this pass.

### Recommendation
Add explicit bounds checks (returning a `Result`/error instead of panicking) before every direct index operation in `decompress()`:
- Validate `header.len() >= 2 + N_UNIQUE_BUCKETS + 1` before slicing.
- Validate each `repeating_value_pointers` entry is `< unique_values.len()`.
- Validate each `bucket_index_per_elm` entry is `< all_bucket_lengths.len()` and that `bucket_offset_trackers[bucket_index] < all_values.len()` before dereferencing.
Mirror the same well-formedness guarantees enforced in-circuit by the Cairo implementation (range checks + `dict_squash` proof of exhaustiveness) so that malformed/adversarial DA data yields a typed error rather than an unhandled panic.

### Proof of Concept
Construct a `compressed: impl Iterator<Item = Felt>` whose header declares `unique_value_bucket_lengths` and `n_repeating_values` inconsistent with the number of felts actually available in the iterator (e.g., truncate the iterator right after the header felt) and call `decompress()` directly [10](#0-9) . Because `unpack_chunk`/`unpack_chunk_to_usize` will return fewer elements than requested when the input iterator is exhausted, the subsequent `header[2..2+N_UNIQUE_BUCKETS]` slice or `all_values[*offset]`/`unique_values[*ptr]` index will panic with "index out of bounds", crashing the calling process — directly analogous to triggering an OOB read/crash via a malformed secondary-order field in rdesktop.

### Citations

**File:** crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs (L543-617)
```rust
/// Decompresses the given compressed data.
#[allow(dead_code)]
pub fn decompress(compressed: &mut impl Iterator<Item = Felt>) -> Vec<Felt> {
    fn unpack_chunk<const LENGTH: usize>(
        compressed: &mut impl Iterator<Item = Felt>,
        n_elms: usize,
    ) -> Vec<Felt> {
        let n_elms_per_felt = BitLength::min_bit_length(LENGTH).unwrap().n_elems_in_felt();
        let n_packed_felts = n_elms.div_ceil(n_elms_per_felt);
        let compressed_chunk: Vec<_> = compressed.take(n_packed_felts).collect();
        unpack_felts(&compressed_chunk, n_elms)
            .into_iter()
            .map(|bits: BitsArray<LENGTH>| felt_from_bits_le(&bits.0).unwrap())
            .collect()
    }

    fn unpack_chunk_to_usize(
        compressed: &mut impl Iterator<Item = Felt>,
        n_elms: usize,
        elm_bound: ElmBoundType,
    ) -> Vec<usize> {
        let n_elms_per_felt = get_n_elms_per_felt(elm_bound);
        let n_packed_felts = n_elms.div_ceil(n_elms_per_felt);

        let compressed_chunk: Vec<_> = compressed.take(n_packed_felts).collect();
        unpack_felts_to::<usize>(&compressed_chunk, n_elms, elm_bound)
    }

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/data_availability/compression.cairo (L34-35)
```text
// Number of bits for each field of the header.
const HEADER_ELM_N_BITS = 20;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/data_availability/compression.cairo (L286-301)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/data_availability/compression.cairo (L411-415)
```text

    // Verify element is in range [0, elm_bound).
    assert [range_check_ptr] = current;
    assert [range_check_ptr + 1] = elm_bound - current - 1;
    let range_check_ptr = range_check_ptr + 2;
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

**File:** crates/starknet_os_flow_tests/src/test_manager.rs (L825-836)
```rust
            // In commitment modes, state diff should be deserialized from the DA segment.
            OsStateDiff::PartialCommitment(_) => {
                let da_segment = runner_output.da_segment.clone().unwrap();
                PartialOsStateDiff::try_from_output_iter(&mut da_segment.into_iter(), private_keys)
                    .unwrap()
                    .as_state_maps()
            }
            OsStateDiff::FullCommitment(_) => {
                let da_segment = runner_output.da_segment.clone().unwrap();
                FullOsStateDiff::try_from_output_iter(&mut da_segment.into_iter(), private_keys)
                    .unwrap()
                    .as_state_maps()
```
