## Finding: Unbounded recursion in `NestedIntList`/bytecode-segment processing during compiled-class hashing

### Title
Unbounded recursion on attacker-influenced `NestedIntList` bytecode-segment structure may cause stack overflow during compiled class hash computation - (File: `crates/starknet_api/src/contract_class/compiled_class_hash.rs`)

### Summary
The CVE describes a stack-overflow DoS caused by unbounded recursion in a recursive-descent parser (`parse_unary`) driven by attacker-supplied nested input. The sequencer contains an analogous pattern: several plain (non-tail, depth-unchecked) recursive functions walk a `NestedIntList`/`HashableNestedIntList` tree describing the CASM bytecode segmentation of a declared class. Unlike Cairo-execution recursion (which is explicitly bounded by `RecursionDepthGuard`/`max_recursion_depth`, see `crates/blockifier/src/execution/entry_point.rs:706-726` and the gas-bounded VM/Native recursion tests), these bytecode-segment recursive helpers have **no depth limit at all**.

### Finding Description
`bytecode_hash_node` recurses once per nesting level of the `bytecode_segment_lengths` field of a `CasmContractClass`: [1](#0-0) 

The equivalent structure-building helper in the OS hint implementation, used during Starknet OS re-execution of a declared class's bytecode hash, is likewise unbounded: [2](#0-1) 

And a third copy exists in the blockifier's execution/contract_class module: [3](#0-2) 

The `bytecode_segment_lengths` value is not attacker-serialized directly, but it is derived by the Sierra→CASM compiler from the *structure* of the declared Sierra program (segmentation follows control-flow/function boundaries), which is fully attacker-controlled input to a `DECLARE` transaction. The compilation and hashing path is invoked for every declared class in `SierraCompiler::compile`: [4](#0-3) 

Unlike the deliberate, explicit recursion-depth protection built for Cairo call-stack recursion (`RecursionDepthGuard`), there is no analogous guard for the depth of the `NestedIntList` tree walked by `bytecode_hash_node` / `create_bytecode_segment_structure_inner` / `NestedFeltCounts::new_inner`. Only aggregate size limits exist (`max_contract_bytecode_size = 81920`, `max_contract_class_object_size`), which bound the total number of felts/bytes but not the *nesting depth* of the segment tree that a pathologically structured Sierra program (e.g., a deeply/sequentially nested control-flow shape) could induce the compiler to emit.

### Impact Explanation
If a contract declarer can cause the compiler to emit a sufficiently deep `NestedIntList`, the recursive hash/segment-structure functions could exhaust the process stack, crashing the sequencer/class-compiler process while validating or executing a `DECLARE` transaction. Because this hashing runs deterministically on every node handling the transaction (gateway/class-manager compilation, blockifier execution, and Starknet OS re-execution/proving), a crash would be reproducible network-wide, potentially halting processing of that transaction across all honest nodes — matching the "network unable to confirm new transactions" impact category.

### Likelihood Explanation
Likelihood is uncertain without confirming how deeply nested a segmentation tree the external Sierra-to-CASM compiler (`cairo-lang-starknet-classes`, an external dependency) can actually be made to emit relative to the 81920-felt bytecode size cap; segmentation width vs. depth characteristics of that compiler were not verifiable from this repository's index. The sequencer-side code itself provides no depth ceiling as a defense-in-depth measure, which is the concrete gap identified here — the actual exploitability hinges on the external compiler's segmentation algorithm, which could not be confirmed with the tools available.

### Recommendation
Add an explicit, enforced maximum depth check (mirroring `RecursionDepthGuard`) to `bytecode_hash_node`, `create_bytecode_segment_structure_inner`, and `NestedFeltCounts::new_inner`, rejecting/erroring out on `NestedIntList` structures whose nesting exceeds a safe bound, and/or convert these functions to iterative (worklist-based) implementations to remove the stack-depth dependency entirely.

### Proof of Concept
Not verifiable end-to-end from the indexed code alone: reproducing the issue requires confirming that the external Sierra-to-CASM compiler can be driven (via a crafted Sierra program within the 81920-felt bytecode limit) to emit a `bytecode_segment_lengths` value with a nesting depth large enough to overflow the stack in `bytecode_hash_node`. This dependency-level detail could not be confirmed with the available tools.

### Citations

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L108-132)
```rust
/// Computes the hash of a bytecode segment. See the documentation of `bytecode_hash_node` in
/// the Starknet OS.
/// Returns the length of the processed segment and its hash.
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
    } else {
        // Compute `1 + poseidon(len0, hash0, len1, hash1, ...)`.
        let inner_nodes = node
            .iter_children()
            .map(|child| bytecode_hash_node::<H, NL>(iter, child))
            .collect_vec();
        let hash = H::hash_array(
            &inner_nodes.iter().flat_map(|(len, hash)| [Felt::from(*len), *hash]).collect_vec(),
        ) + Felt::ONE;
        (inner_nodes.iter().map(|(len, _)| len).sum(), hash)
    }
}
```

**File:** crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs (L275-307)
```rust
/// Helper function for `create_bytecode_segment_structure`.
/// Returns the bytecode segment structure and the total length of the processed segment.
pub(crate) fn create_bytecode_segment_structure_inner(
    bytecode: &[Felt],
    bytecode_segment_lengths: NestedIntList,
    bytecode_offset: usize,
) -> (BytecodeSegmentNode, usize) {
    match bytecode_segment_lengths {
        NestedIntList::Leaf(length) => {
            let segment_end = bytecode_offset + length;
            let bytecode_segment = bytecode[bytecode_offset..segment_end].to_vec();

            (BytecodeSegmentNode::Leaf(BytecodeSegmentLeaf { data: bytecode_segment }), length)
        }
        NestedIntList::Node(lengths) => {
            let mut segments = vec![];
            let mut total_len = 0;
            let mut bytecode_offset = bytecode_offset;

            for item in lengths {
                let (current_structure, item_len) =
                    create_bytecode_segment_structure_inner(bytecode, item, bytecode_offset);

                segments.push(BytecodeSegment { length: item_len, node: current_structure });

                bytecode_offset += item_len;
                total_len += item_len;
            }

            (BytecodeSegmentNode::InnerNode(BytecodeSegmentInnerNode { segments }), total_len)
        }
    }
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

**File:** crates/apollo_compile_to_casm/src/lib.rs (L58-74)
```rust
    #[instrument(skip(self, class), err)]
    #[sequencer_latency_histogram(COMPILATION_DURATION, true)]
    pub fn compile(&self, class: RawClass) -> SierraCompilerResult<RawExecutableHashedClass> {
        let class = SierraContractClass::try_from(class)?;
        let sierra_version =
            class.get_sierra_version().map_err(SierraCompilerError::SierraVersionFormat)?;
        let class = into_contract_class_for_compilation(&class);

        // TODO(Elin): handle resources (whether here or an infra. layer load-balancing).
        let executable_class = self.compiler.compile(class)?;
        // TODO(Elin): consider spawning a worker for hash calculation.
        let executable_class_hash = executable_class.hash(&HashVersion::V2);
        let executable_class = ContractClass::V1((executable_class, sierra_version));
        let executable_class = RawExecutableClass::try_from(executable_class)?;

        Ok((executable_class, executable_class_hash))
    }
```
