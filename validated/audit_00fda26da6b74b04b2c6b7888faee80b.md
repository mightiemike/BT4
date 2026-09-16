### Title
Uncontrolled recursion in `bytecode_hash_node` during compiled-class-hash computation - (File: crates/starknet_api/src/contract_class/compiled_class_hash.rs)

### Summary
`bytecode_hash_node` recursively walks the `NestedIntList` bytecode-segment-lengths structure of a `CasmContractClass` with no depth bound, mirroring the Apache Neethi CVE-2026-66142 pattern (deeply-nested policy structure parsed with unbounded recursion, causing stack exhaustion). This structure is derived by the sequencer's own Sierra→CASM compiler from an attacker-supplied Sierra program submitted in a `DECLARE` transaction, so its nesting depth is indirectly influenced by the shape of the declared program.

### Finding Description
`bytecode_hash_node` recurses once per nesting level of `NestedIntList`/`HashableNestedIntList` with no depth guard: [1](#0-0) 

This function is invoked from `hash_inner`, which is the implementation behind `HashableCompiledClass::hash`, used to compute the compiled class hash of a `CasmContractClass`: [2](#0-1) 

The `bytecode_segment_lengths` field consumed here comes directly from the `CasmContractClass` produced by compiling an attacker-submitted Sierra program (declared via a `DECLARE` transaction), as seen in the gateway's compile pipeline: [3](#0-2) 

Notably, a parallel/duplicate implementation of the same recursive-descent pattern in the blockifier (`NestedFeltCounts::new_inner`) explicitly *asserts* that segmentation depth is bounded to at most 1, and panics otherwise: [4](#0-3) 

This assertion strongly implies the code authors assumed the compiler-produced `NestedIntList` should never exceed depth 1 in normal use — yet `bytecode_hash_node` in `starknet_api` enforces no such bound at all, and the Starknet OS's own `create_bytecode_segment_structure_inner` (used for the analogous OS-side hint) is likewise unbounded: [5](#0-4) 

Because the actual segmentation tree returned by the Cairo Sierra→CASM compiler is a function of the program's control-flow/function structure (not validated or capped by the sequencer beyond overall bytecode size limits like `max_bytecode_size`), a maliciously structured but otherwise valid Sierra program could induce the compiler to emit a `bytecode_segment_lengths` tree many levels deep. Every recursive call to `bytecode_hash_node` (and to the OS/blockifier counterparts) consumes native call-stack frames; with sufficient nesting this can exhaust the stack, causing every node that performs the hash computation to crash (the classic uncontrolled-recursion DoS pattern in the report).

### Impact Explanation
The compiled class hash is computed by every node that processes a `DECLARE` transaction — in the gateway during stateless/stateful validation, in the blockifier during declare-tx execution, and again by the Starknet OS during block proving/re-execution. A stack overflow here is a process crash, not a Rust panic that can be caught: it would take down the sequencer/full-node process handling the transaction, which for a permissionless `DECLARE` submission constitutes a network-wide denial-of-service vector (every honest node re-executing/re-validating the same class would crash identically), potentially halting block production or proof generation.

### Likelihood Explanation
The exact depth achievable is unconfirmed without deeper analysis of the actual `cairo-lang` compiler's bytecode-segmentation algorithm (which is outside this repo, imported as `cairo_lang_starknet_classes`), so I cannot fully confirm from this codebase alone whether the compiler can be induced to emit deeply nested (e.g., thousands of levels) `NestedIntList` trees from a Sierra program within the declared bytecode-size limit (`max_bytecode_size` in `SierraCompilationConfig`). The presence of the `assert!(segmentation_depth <= 1, ...)` in the blockifier path is circumstantial evidence that the authors believed real-world segmentation depth from the compiler is normally shallow (≤1), which — if true — would mean this bug class is not practically reachable at dangerous depth in the current implementation. This uncertainty means the finding should be treated as a plausible-but-unconfirmed analog rather than a proven exploit.

### Recommendation
Add an explicit, enforced maximum recursion/nesting depth check when computing `bytecode_hash` / `bytecode_hash_node` (and the equivalent OS-side and blockifier-side segment-tree walkers), rejecting or erroring out on `NestedIntList` structures that exceed a safe depth rather than recursing unboundedly. Alternatively, convert the recursive descent into an explicit iterative (stack-based) traversal to remove the native call-stack dependency altogether. This should be applied consistently across `crates/starknet_api/src/contract_class/compiled_class_hash.rs`, `crates/blockifier/src/execution/contract_class.rs` (`NestedFeltCounts::new_inner`), and `crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs` (`create_bytecode_segment_structure_inner`).

### Proof of Concept
Not independently constructed/verified in this analysis — doing so requires driving the external `cairo_lang_starknet_classes` Sierra→CASM compiler (not part of this repo) with a crafted Sierra program to confirm it can produce a `bytecode_segment_lengths: NestedIntList` of large nesting depth within the sequencer's `max_bytecode_size` bound, then submitting that class via a `DECLARE` transaction and observing whether `bytecode_hash_node` recursion depth causes a stack overflow. This step could not be completed with the available read-only codebase tools.

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

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L153-178)
```rust
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
