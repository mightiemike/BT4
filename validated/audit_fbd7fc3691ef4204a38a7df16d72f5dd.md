## Title
Unbounded recursion in `bytecode_hash_node` compiled-class-hash computation allows a stack-overflow DoS via attacker-controlled bytecode segment structure - (File: `crates/starknet_api/src/contract_class/compiled_class_hash.rs`)

### Summary
`bytecode_hash_node` in `crates/starknet_api/src/contract_class/compiled_class_hash.rs` recursively walks a `NestedIntList`/`HashableNestedIntList` bytecode-segmentation tree with no recursion-depth bound, mirroring the Suricata `http-body-printable` bug class: an attacker-influenced, size/structure-controlled input is processed by unbounded recursion, risking a native stack overflow rather than a graceful error. [1](#0-0) 

### Finding Description
`bytecode_hash_node` recurses once per nesting level of the `bytecode_segment_lengths` structure that accompanies a compiled (CASM) contract class, with no depth guard analogous to the one used elsewhere in the codebase (`blockifier`'s `NestedFeltCounts::new_inner` explicitly asserts `segmentation_depth <= 1`): [2](#0-1) 

In contrast, `bytecode_hash_node` (used for compiled-class-hash computation, i.e. `HashableCompiledClass`) and the Starknet-OS equivalent `create_bytecode_segment_structure_inner` have no such assertion and recurse purely based on the shape of the `NestedIntList` supplied by the class: [3](#0-2) 

This `bytecode_segment_lengths` value is produced by the Sierra→CASM compiler (`cairo_lang_starknet_classes`) from a **declarer-supplied Sierra program** — the compiler segments bytecode along function/branch boundaries, so a contract crafted with a very large number of deeply/adjacently nested functions or branches can induce many nesting levels in the resulting `NestedIntList` tree. The compiled-class hash is computed for every declared class (to verify `compiled_class_hash` on `DECLARE` transactions) and again by the Starknet OS during re-execution/proving, meaning the recursive hashing routine runs on the sequencer's real call stack, not inside the Cairo VM's heap-simulated stack — unlike Cairo contract call recursion, which is bounded by `RecursionDepthGuard`/`max_recursion_depth`: [4](#0-3) 

There is no equivalent guard on `bytecode_hash_node`'s recursion depth; the only existing protections are the flat bytecode-size and CASM object-size limits (`max_bytecode_size`, `max_compiled_contract_class_object_size`), which bound total data volume but not tree nesting depth: [5](#0-4) [6](#0-5) 

Neither of these limits prevents an attacker from choosing a Sierra program whose compiled bytecode segments naturally nest to a large depth relative to its size (e.g., via long chains of nested branches/inline functions), producing a `NestedIntList` deep enough to overflow the sequencer process's native call stack when `bytecode_hash_node` (or the OS's `create_bytecode_segment_structure_inner`) recurses over it.

### Impact Explanation
A stack overflow in a sequencer/full-node process crashes that process (not a controlled revert). If triggered during compiled-class-hash validation on `DECLARE` transaction processing, or during Starknet-OS re-execution/proving of a block containing such a declared class, this can crash the sequencer or prover, halting production/verification of blocks and preventing the network from confirming new transactions — a network-availability impact, reachable from a single, unprivileged `DECLARE` transaction.

### Likelihood Explanation
Reachability requires only a normal, unprivileged `DECLARE` transaction with an adversarially structured (but otherwise valid, size-limited) Sierra program compiled by the standard compiler pipeline; no special privileges are needed. The likelihood of actually achieving stack overflow depends on unverified specifics: (1) whether the Cairo compiler's segmentation logic actually permits deep-enough nesting within the existing bytecode-size limits to exhaust a typical thread stack, and (2) the default stack size used by the sequencer's execution threads for this codepath (only the Cairo Native execution path was found to have deliberately allocated stack-size handling; no equivalent was found for `bytecode_hash_node`). These are not confirmed in this pass.

### Recommendation
Add an explicit depth bound (or convert to an iterative/worklist algorithm) in `bytecode_hash_node` (`crates/starknet_api/src/contract_class/compiled_class_hash.rs`) and in the Starknet-OS `create_bytecode_segment_structure_inner` (`crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs`), analogous to the `segmentation_depth <= 1` assertion already present in `blockifier`'s `NestedFeltCounts::new_inner`, and reject compiled classes whose segment-length structure exceeds the bound during declare validation.

### Proof of Concept
Not independently constructed/verified in this pass — would require confirming with the Cairo compiler that a Sierra program within existing size limits can produce a `bytecode_segment_lengths` `NestedIntList` with nesting depth sufficient to overflow the sequencer's default thread stack when hashed via `bytecode_hash_node`.

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

**File:** crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs (L277-307)
```rust
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

**File:** crates/blockifier/src/execution/entry_point.rs (L706-726)
```rust
// Ensure that the recursion depth does not exceed the maximum allowed depth.
struct RecursionDepthGuard {
    current_depth: Arc<RefCell<usize>>,
    max_depth: usize,
}

impl RecursionDepthGuard {
    fn new(current_depth: Arc<RefCell<usize>>, max_depth: usize) -> Self {
        Self { current_depth, max_depth }
    }

    // Tries to increment the current recursion depth and returns an error if the maximum depth
    // would be exceeded.
    fn try_increment_and_check_depth(&mut self) -> Result<(), EntryPointExecutionError> {
        *self.current_depth.borrow_mut() += 1;
        if *self.current_depth.borrow() > self.max_depth {
            return Err(EntryPointExecutionError::RecursionDepthExceeded);
        }
        Ok(())
    }
}
```

**File:** crates/apollo_compile_to_casm/src/compiler.rs (L30-55)
```rust
    pub fn compile(
        &self,
        contract_class: ContractClass,
    ) -> Result<CasmContractClass, CompilationUtilError> {
        let compiler_binary_path = &self.path_to_binary;
        let additional_args = &[
            "--add-pythonic-hints",
            "--max-bytecode-size",
            &self.config.max_bytecode_size.to_string(),
            "--allowed-libfuncs-list-name",
            if self.config.audited_libfuncs_only { "audited" } else { "all" },
        ];
        let resource_limits = ResourceLimits::new(
            Some(self.config.max_cpu_time),
            None,
            Some(self.config.max_memory_usage),
        );

        let stdout = compile_with_args(
            compiler_binary_path,
            contract_class,
            additional_args,
            resource_limits,
        )?;
        Ok(serde_json::from_slice::<CasmContractClass>(&stdout)?)
    }
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L157-178)
```rust
    fn validate_class_length(
        &self,
        serialized_class: &RawExecutableClass,
    ) -> ClassManagerResult<()> {
        // Note: The class bytecode length is validated in the compiler.

        let contract_class_object_size =
            serialized_class.size().expect("Unexpected error serializing contract class.");
        if contract_class_object_size
            > self.config.static_config.class_manager_config.max_compiled_contract_class_object_size
        {
            return Err(ClassManagerError::ContractClassObjectSizeTooLarge {
                contract_class_object_size,
                max_contract_class_object_size: self
                    .config
                    .static_config
                    .class_manager_config
                    .max_compiled_contract_class_object_size,
            });
        }

        Ok(())
```
