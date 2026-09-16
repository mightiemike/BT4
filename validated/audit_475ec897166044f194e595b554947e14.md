Confirmed: `CompiledClassV1::try_from((casm_compiled_class, sierra_version))` at `crates/apollo_state_reader/src/apollo_state.rs:169` is invoked on every state read of a Cairo-1 compiled class — i.e., on every transaction that touches a declared class (executing an invoke against it, or the declare itself). This flows into `TryFrom<VersionedCasm> for CompiledClassV1` in `crates/blockifier/src/execution/contract_class.rs:626-668`, which unconditionally calls `NestedFeltCounts::new(&class.get_bytecode_segment_lengths(), &class.bytecode)`.

### Title
Panic (DoS) via out-of-bounds slice index in `NestedFeltCounts::new_inner` when compiled-class bytecode segment lengths are inconsistent with bytecode - (File: crates/blockifier/src/execution/contract_class.rs)

### Summary
`NestedFeltCounts::new_inner` indexes `bytecode[..*len]` and `bytecode[total_felt_count..]` using segment lengths taken directly from `bytecode_segment_lengths` (a `NestedIntList` sourced from the CASM's own `bytecode_segment_lengths` field) with no bounds check before slicing. If any `Leaf(len)` value exceeds the number of felts actually remaining in `bytecode`, the slice operation panics with an out-of-bounds index, analogous to the dhowden/tag CWE-129 out-of-bounds read panic.

### Finding Description [1](#0-0) 

`new_inner` recurses over the `NestedIntList` structure, and for a `Leaf(len)` slices `&bytecode[..*len]` (line 172) without verifying `len <= bytecode.len()`. The `Node` branch also slices `&bytecode[total_felt_count..]` (line 183) which can panic if `total_felt_count > bytecode.len()`. This function is invoked from `TryFrom<VersionedCasm> for CompiledClassV1::try_from` unconditionally, before any consistency check: [2](#0-1) 

Notably, the public `new()` wrapper only asserts consistency via `assert_eq!(consumed_felts, bytecode.len())` *after* the recursive slicing has already happened — so any mismatch causing an early out-of-bounds slice panics before that assertion is ever reached: [3](#0-2) 

This `TryFrom<VersionedCasm>` conversion is on the hot path for every state read of a declared Cairo-1 class in `apollo_state_reader`: [4](#0-3) 

For comparison, `starknet_api`'s equivalent bytecode-hash traversal (`bytecode_hash_node`) also panics on mismatch via `assert_eq!` rather than gracefully erroring: [5](#0-4) 
whereas the Starknet OS hint implementation for the same nested structure does perform a length check and returns a structured error instead of panicking: [6](#0-5) 
This inconsistency across the codebase (checked-and-error in OS hints vs. unchecked-and-panic in blockifier/starknet_api) mirrors the exact bug class of the referenced advisory: parsing code trusting length/offset fields taken from the input without validating them against the actual buffer size before indexing.

### Impact Explanation
I was not able to conclusively determine, within the available tool budget, a concrete production code path where the `bytecode_segment_lengths` field of a `CasmContractClass` can be made inconsistent with its `bytecode` field by an unprivileged declarer. In the primary gateway flow (`crates/apollo_compile_to_casm/src/compiler.rs` and `crates/apollo_compile_to_casm/src/lib.rs`), `bytecode_segment_lengths` is derived by the trusted Cairo compiler binary from the same Sierra program that produces `bytecode`, so under normal compiler behavior the two should be self-consistent. Whether a maliciously crafted Sierra program can induce the compiler to emit an internally-inconsistent `CasmContractClass` (segment lengths not summing to `bytecode.len()`), or whether any other path (e.g. sync from peer nodes, or JSON round-tripping through storage) can inject such a `CasmContractClass` without re-validating segment-length consistency before this conversion, is unconfirmed. If such a path exists, the impact would be a process panic on that node whenever it reads the affected class for execution — a potential denial-of-service that could recur on every re-execution/re-sync of a block containing the malformed class (honest-node divergence is unlikely since panic would be deterministic across nodes, but it could crash sequencer/full-node processes handling that class).

### Likelihood Explanation
Low-to-uncertain. The bug class (unchecked slice indexing derived from attacker-influenced-but-normally-compiler-controlled length fields) is real and structurally identical to the CWE-129 report, and the code lacks defensive bounds checking that exists in the analogous OS-hint code path. However, I could not confirm a concrete unprivileged trigger (a specific malformed Sierra/CASM class) that reaches this code with `bytecode_segment_lengths` inconsistent with `bytecode`, since normal compilation ties the two together. This is a data-consistency assumption that is currently enforced only by an `assert_eq!` *after* the vulnerable indexing, rather than validated before it — a latent defensive-coding gap rather than a demonstrated reachable exploit.

### Recommendation
Validate that all leaf lengths in `bytecode_segment_lengths` are within bounds of `bytecode` before indexing, returning a `Result`/proper error (e.g. `ProgramError`) instead of panicking, mirroring the checked approach already used in `create_bytecode_segment_structure` (`crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs:258-273`). Specifically, in `NestedFeltCounts::new_inner` (`crates/blockifier/src/execution/contract_class.rs:171-173, 183`), check `*len <= bytecode.len()` (and similarly for the remaining-slice case) and propagate an error rather than slicing unconditionally, and change `NestedFeltCounts::new`/`TryFrom<VersionedCasm>` to surface that error instead of asserting after the fact.

### Proof of Concept
Not independently verified end-to-end due to tool-call limits. Conceptually: construct a `CasmContractClass` whose `bytecode_segment_lengths` contains a `Leaf(len)` with `len` greater than `bytecode.len()` (or whose nested lengths sum to more than `bytecode.len()`), and pass it through `CompiledClassV1::try_from((casm, sierra_version))` — reproducible directly as a unit test using the existing test helper `get_dummy_compiled_class` pattern in `crates/starknet_os/src/hints/hint_implementation/compiled_class/compiled_class_test.rs:208-242`, by supplying an oversized `bytecode_segment_lengths` value and calling `NestedFeltCounts::new` (or the `TryFrom` conversion) directly, which should panic with an out-of-bounds slice index rather than return an error.

### Citations

**File:** crates/blockifier/src/execution/contract_class.rs (L156-160)
```rust
    pub fn new(bytecode_segment_lengths: &NestedIntList, bytecode: &[BigUintAsHex]) -> Self {
        let (base_node, consumed_felts) = Self::new_inner(bytecode_segment_lengths, bytecode, 0);
        assert_eq!(consumed_felts, bytecode.len());
        base_node
    }
```

**File:** crates/blockifier/src/execution/contract_class.rs (L162-194)
```rust
    /// Recursively builds the nested structure and returns it with the number of items consumed.
    fn new_inner(
        bytecode_segment_lengths: &NestedIntList,
        bytecode: &[BigUintAsHex],
        segmentation_depth: usize,
    ) -> (Self, usize) {
        assert!(segmentation_depth <= 1, "Only supported for segmentation depth at most 1.");

        match bytecode_segment_lengths {
            NestedIntList::Leaf(len) => {
                let felt_size_groups = FeltSizeCount::from(&bytecode[..*len]);
                (NestedFeltCounts::Leaf(*len, felt_size_groups), *len)
            }
            NestedIntList::Node(segments_vec) => {
                let mut total_felt_count = 0;
                let mut segments = Vec::with_capacity(segments_vec.len());

                for segment in segments_vec {
                    // Recurse into the segment layout.
                    let (segment, felt_count) = Self::new_inner(
                        segment,
                        &bytecode[total_felt_count..],
                        segmentation_depth + 1,
                    );
                    // Accumulate the count from the segment`s subtree.
                    total_felt_count += felt_count;
                    segments.push(segment);
                }

                (NestedFeltCounts::Node(segments), total_felt_count)
            }
        }
    }
```

**File:** crates/blockifier/src/execution/contract_class.rs (L667-668)
```rust
        let bytecode_segment_felt_sizes =
            NestedFeltCounts::new(&class.get_bytecode_segment_lengths(), &class.bytecode);
```

**File:** crates/apollo_state_reader/src/apollo_state.rs (L163-171)
```rust
    fn get_compiled_class_from_db(&self, class_hash: ClassHash) -> StateResult<CompiledClasses> {
        if self.is_declared(class_hash)? {
            // Cairo 1.
            let (casm_compiled_class, sierra) = self.read_casm_and_sierra(class_hash)?;
            let sierra_version = sierra.get_sierra_version()?;
            return Ok(CompiledClasses::V1(
                CompiledClassV1::try_from((casm_compiled_class, sierra_version))?,
                Arc::new(sierra),
            ));
```

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L111-120)
```rust
fn bytecode_hash_node<H, NL>(iter: &mut impl Iterator<Item = Felt>, node: &NL) -> (usize, Felt)
where
    H: StarkHash,
    NL: HashableNestedIntList,
{
    if node.is_leaf() {
        let len = node.get_segment_length();
        let data = iter.take(len).collect_vec();
        assert_eq!(data.len(), len);
        (len, H::hash_array(&data))
```

**File:** crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs (L258-273)
```rust
) -> Result<BytecodeSegmentNode, OsHintError> {
    let (structure, total_len) =
        create_bytecode_segment_structure_inner(bytecode, bytecode_segment_lengths, 0);
    // Sanity checks.
    if total_len != bytecode.len() {
        return Err(OsHintError::AssertionFailed {
            message: format!(
                "Invalid length bytecode segment structure: {}. Bytecode length: {}.",
                total_len,
                bytecode.len()
            ),
        });
    }

    Ok(structure)
}
```
