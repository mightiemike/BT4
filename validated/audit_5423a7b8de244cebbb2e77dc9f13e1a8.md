### Title
Unbounded recursive descent over declared-class `bytecode_segment_lengths` causes stack overflow / DoS during compiled class hash computation - (File: `crates/starknet_api/src/contract_class/compiled_class_hash.rs`)

### Summary
`bytecode_hash_node` in `crates/starknet_api/src/contract_class/compiled_class_hash.rs` recursively walks the `NestedIntList` structure (`bytecode_segment_lengths`) of a `CasmContractClass` to compute the compiled class hash, with no bound on nesting depth. [1](#0-0)  This is the same bug class as the reported PaddlePaddle `lu_unpack` stack overflow: a data-driven recursive routine with no depth cap, reachable from externally supplied structured input.

### Finding Description
`bytecode_hash_node` recurses once per nesting level of `NestedIntList::Node` children with no depth limit or iterative fallback: [2](#0-1) 
This function is invoked from `bytecode_hash`, called from `hash_inner`, called from `HashableCompiledClass::hash`, which is the routine used to derive/verify the `compiled_class_hash` of a `CasmContractClass`. [3](#0-2)  The equivalent recursive segment-hashing logic also appears independently in the blockifier (`NestedFeltCounts::new_inner`) and in the Starknet OS hint implementation (`create_bytecode_segment_structure_inner`, `BytecodeSegmentNode::hash`), all of which walk the same untrusted nested structure without any explicit recursion-depth guard, unlike entry-point call recursion which is protected by `RecursionDepthGuard`/`max_recursion_depth` in the blockifier. [4](#0-3) [5](#0-4) [6](#0-5) 

The recursion depth is controlled by the *shape* of `bytecode_segment_lengths`, a nested-list of integers. Whether this field is generated purely by the trusted, resource-limited Sierra→Casm compiler subprocess (`apollo_compile_to_casm`, bounded by `max_bytecode_size`/`max_cpu_time`/`max_memory_usage`) is the key open question I could not fully resolve: [7](#0-6)  if the resulting `CasmContractClass` (including `bytecode_segment_lengths`) is only ever produced server-side by that sandboxed compiler and never accepted directly from an untrusted declare-transaction payload, then the nesting depth is implicitly bounded by however deeply the compiler itself can nest segments for a bytecode capped at `max_bytecode_size`. I did not find, however, any explicit assertion in the deserialization path (`apollo_storage` serializers, RPC read path) that caps the *depth* of `NestedIntList` independently of overall size — a compiler that generates deeply right-recursive/nested segment trees for adversarially structured but small Sierra bytecode (e.g., deeply nested branch/function structure) could still produce enough nesting to overflow the stack in `bytecode_hash_node`, which runs on every node's fully synchronous Rust call stack (no `#[async_recursion]`/spawned-task indirection as used to bound stack growth in the Patricia tree code) [8](#0-7) .

### Impact Explanation
If a class declarer can shape a contract's compiled bytecode so that the compiler emits a sufficiently deep `bytecode_segment_lengths` nesting, computing the compiled class hash (done by every sequencer/full node re-executing or re-validating the declare transaction, and again by the Starknet OS during block proving) would recurse without bound and crash the process with a stack overflow. Because this hash computation happens both when validating a `DECLARE` transaction and again during Starknet OS re-execution, a crash here could halt block production/validation on every honest node processing the same class — a network-wide denial of service, not merely a single validator.

### Likelihood Explanation
Likelihood is uncertain and depends on facts I could not verify from the index: (1) whether `bytecode_segment_lengths` nesting depth for a given bytecode size is meaningfully bounded by the trusted compiler's segmentation algorithm (which segments code along function/branch boundaries, typically much shallower than bytecode length), and (2) whether any additional depth cap exists elsewhere in the pipeline (e.g., Cairo compiler's own libfunc/program size limits). Without confirming these bounds, I cannot assert this is concretely exploitable by an unprivileged declarer through the documented compilation path; it is a plausible but unconfirmed analog.

### Recommendation
Add an explicit maximum recursion-depth check (or convert `bytecode_hash_node`, `NestedFeltCounts::new_inner`, and `create_bytecode_segment_structure_inner` to an iterative/worklist implementation) mirroring the `RecursionDepthGuard` pattern already used for contract call recursion, and validate `bytecode_segment_lengths` nesting depth at class-declaration/deserialization time before it reaches these hashing routines.

### Proof of Concept
Not constructible from available context — I could not confirm from the index whether `bytecode_segment_lengths` can, in practice, be driven to unbounded nesting depth by a class declarer through the standard `apollo_compile_to_casm` compilation path (this depends on internal behavior of the `cairo-lang-starknet-classes`/`cairo-lang-sierra-to-casm` segmentation algorithm, which is a dependency not fully indexed here). This is flagged as an analog worth validating with a live compilation experiment (declare a contract with deeply nested Sierra control flow and inspect the resulting `bytecode_segment_lengths` nesting depth) rather than a proven exploit.

### Citations

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L111-132)
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

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L144-178)
```rust
    fn hash(&self, hash_version: &HashVersion) -> CompiledClassHash {
        match hash_version {
            HashVersion::V1 => hash_inner::<Poseidon, EH, NL>(self),
            HashVersion::V2 => hash_inner::<Blake2Felt252, EH, NL>(self),
        }
    }
}

/// Computes the compiled class hash for a given hashable class using the specified hash algorithm.
fn hash_inner<H, EH, NL>(hashable_class: &impl HashableCompiledClass<EH, NL>) -> CompiledClassHash
where
    H: StarkHash,
    EH: EntryPointHashable,
    NL: HashableNestedIntList,
{
    let external_funcs_hash =
        entry_point_hash::<H, EH>(hashable_class.get_hashable_external_entry_points());
    let l1_handlers_hash = entry_point_hash::<H, EH>(hashable_class.get_hashable_l1_entry_points());
    let constructors_hash =
        entry_point_hash::<H, EH>(hashable_class.get_hashable_constructor_entry_points());

    let bytecode_hash = bytecode_hash::<H, NL>(
        &hashable_class.get_bytecode(),
        &*hashable_class.get_bytecode_segment_lengths(),
    );

    // Compute total hash by hashing each component on top of the previous one.
    CompiledClassHash(H::hash_array(&[
        *COMPILED_CLASS_V1,
        external_funcs_hash,
        l1_handlers_hash,
        constructors_hash,
        bytecode_hash,
    ]))
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

**File:** crates/starknet_patricia/src/patricia_merkle_tree/filled_tree/tree.rs (L181-192)
```rust
    #[async_recursion]
    async fn compute_filled_tree_rec<'a, TH>(
        updated_skeleton: Arc<impl UpdatedSkeletonTree<'a> + 'async_recursion + 'static>,
        index: NodeIndex,
        leaf_modifications: Option<Arc<LeafModifications<L>>>,
        leaf_index_to_leaf_input: Arc<HashMap<NodeIndex, Mutex<Option<L::Input>>>>,
        filled_tree_output_map: Arc<HashMap<NodeIndex, Mutex<Option<HashFilledNode<L>>>>>,
        leaf_index_to_leaf_output: Arc<HashMap<NodeIndex, Mutex<Option<L::Output>>>>,
    ) -> FilledTreeResult<HashOutput>
    where
        TH: TreeHashFunction<L> + 'static,
    {
```
