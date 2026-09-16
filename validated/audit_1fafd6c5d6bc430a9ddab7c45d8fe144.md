### Title
Unbounded recursion in `NestedIntList` bytecode-segment hashing/traversal enables stack-overflow DoS on class declaration and OS re-execution - (File: crates/starknet_api/src/contract_class/compiled_class_hash.rs)

### Summary
The CVE describes a stack-overflow DoS caused by unbounded recursive parsing of an attacker-supplied value. The sequencer has an analogous unbounded recursion in `bytecode_hash_node` (used to compute the CASM compiled-class hash) and in `create_bytecode_segment_structure_inner` (used by the Starknet OS to build the bytecode segment tree for hashing/loading during re-execution). Both recurse directly over the depth of a `NestedIntList` (`bytecode_segment_lengths`) with no depth limit, unlike the sibling function `NestedFeltCounts::new_inner`, which explicitly `assert!(segmentation_depth <= 1, ...)`.

### Finding Description
`bytecode_hash_node` in [1](#0-0)  recurses once per nesting level of `node.iter_children()`, with no explicit bound on recursion depth — the only constraint is implicit in how deeply nested the `NestedIntList` structure is. This function is invoked from `hash_inner`/`HashableCompiledClass::hash`, which is the code path used to compute `CompiledClassHash` for every declared Cairo1 class, e.g. in [2](#0-1)  and in the class hashing utility used during class provisioning [3](#0-2) .

The Starknet OS mirrors this pattern with `create_bytecode_segment_structure_inner`, which likewise recurses unboundedly over `NestedIntList::Node` children: [4](#0-3) . This function is exercised whenever compiled classes are loaded for OS re-execution/proving, via `load_classes_and_create_bytecode_segment_structures`: [5](#0-4) .

By contrast, the blockifier's own `NestedFeltCounts::new_inner`, which processes the same `NestedIntList` type for a different purpose (loaded-segment PC bookkeeping), explicitly documents and enforces that only a segmentation depth of at most 1 is supported: [6](#0-5) . This inconsistency indicates the codebase's own invariant is "segment nesting depth ≤ 1", yet the hash/segment-structure code paths do not enforce or even check this invariant before recursing.

`bytecode_segment_lengths` is stored as part of a `CasmContractClass` (`Option<NestedIntList>`) that a class declarer indirectly influences: the sequencer compiles the declarer's Sierra program into CASM (Sierra-to-CASM compilation is out of this repo's scope — it lives in the `cairo-lang-starknet-classes` dependency), and the resulting segment structure's nesting reflects the branch/segment structure the compiler derives from the submitted program. The gateway's stateless validation only bounds total Sierra program length and serialized class size (`max_contract_bytecode_size`, `max_contract_class_object_size`) — [7](#0-6)  — but does not bound or validate the *nesting depth* of the compiler-produced `bytecode_segment_lengths` tree before it is later fed into the unbounded recursive hashing/traversal functions.

### Impact Explanation
If a declarer can produce (via crafted Sierra source, compiled by the trusted sequencer compiler) a `NestedIntList` whose nesting depth is large enough, both:
- the compiled-class-hash computation (`bytecode_hash_node`) invoked at declaration/storage time, and
- the OS bytecode-segment-structure construction (`create_bytecode_segment_structure_inner`) invoked during every re-execution/proving pass that touches that class,

recurse to a depth proportional to that nesting, with each frame allocating a `Vec` and doing further work. Deep-enough nesting can exhaust the thread stack and crash the process (sequencer, prover, or any node re-executing/re-hashing the class), which is a denial-of-service against block production/validation and OS re-execution — i.e., "a network unable to confirm new transactions" for nodes/sequencers/provers processing that declared class.

### Likelihood Explanation
The likelihood depends entirely on whether the Sierra-to-CASM compiler (external `cairo-lang-starknet-classes` dependency, not present in this repo) can be coerced into emitting a `NestedIntList` with pathological nesting depth from an adversarial-but-valid Sierra program (e.g., deeply nested branch/segment boundaries), while staying within the gateway's total-size limits (`max_contract_bytecode_size` = 81920 felts by default: [8](#0-7) ). This repo's own internal invariant comment ("Only supported for segmentation depth at most 1") suggests the compiler is expected/designed to produce shallow trees in practice, which would make deep-nesting infeasible through normal compilation. I could not verify the actual maximum nesting depth the compiler can produce since that logic is external to this repository — this is a real gap in my analysis, not something I can resolve from the indexed code alone.

### Recommendation
- Add an explicit depth check (mirroring `NestedFeltCounts::new_inner`'s `segmentation_depth <= 1` assumption) to `bytecode_hash_node` (`compiled_class_hash.rs`) and `create_bytecode_segment_structure_inner` (`starknet_os` `utils.rs`) that rejects/short-circuits `NestedIntList` values whose nesting exceeds the expected maximum, returning an error instead of recursing further.
- Alternatively/additionally, convert both recursive functions to an explicit iterative traversal with an internal stack, removing dependence on the call stack entirely.
- Validate the compiled `bytecode_segment_lengths` structure's depth in the gateway/blockifier immediately after Sierra→CASM compilation, before it is persisted or hashed, so malformed/pathological structures are rejected at declaration time rather than being propagated to consumers (storage hashing, OS re-execution).

### Proof of Concept
Not constructible from this repository alone: the component that actually produces `bytecode_segment_lengths` nesting depth from a Sierra program (the Sierra-to-CASM compiler in `cairo-lang-starknet-classes`) is an external dependency not indexed here, so I cannot confirm from this codebase whether an attacker-supplied Sierra program can drive that compiler to emit a `NestedIntList` deep enough to overflow the stack in `bytecode_hash_node`/`create_bytecode_segment_structure_inner`. A concrete PoC would require testing against that compiler directly (e.g., a Devin session with the ability to run the compiler and inspect worst-case segment nesting for crafted Sierra input) — this is outside what I can determine through static code search.

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

**File:** crates/native_blockifier/src/storage.rs (L199-200)
```rust
                let casm_contract_class: CasmContractClass = serde_json::from_str(&raw_casm)?;
                let compiled_class_hash_v2 = casm_contract_class.hash(&HashVersion::V2);
```

**File:** crates/starknet_transaction_prover/src/running/classes_provider.rs (L50-58)
```rust
    Ok(CasmContractClass {
        prime,
        compiler_version: String::new(),
        bytecode,
        bytecode_segment_lengths: Some(class.bytecode_segment_felt_sizes().into()),
        hints: program_hints_to_casm_hints(&class.program.shared_program_data.hints_collection)?,
        pythonic_hints: None,
        entry_points_by_type: (&class.entry_points_by_type).into(),
    })
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

**File:** crates/starknet_os/src/hints/hint_implementation/compiled_class/implementation.rs (L211-217)
```rust
        bytecode_segment_structures.insert(
            *compiled_class_hash,
            create_bytecode_segment_structure(
                &compiled_class.bytecode.iter().map(|x| Felt::from(&x.value)).collect::<Vec<_>>(),
                compiled_class.get_bytecode_segment_lengths(),
            )?,
        );
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L315-337)
```rust
    fn validate_class_length(
        &self,
        contract_class: &starknet_api::state::SierraContractClass,
    ) -> StatelessTransactionValidatorResult<()> {
        if contract_class.sierra_program.len() > self.config.max_contract_bytecode_size {
            return Err(StatelessTransactionValidatorError::ContractBytecodeSizeTooLarge {
                contract_bytecode_size: contract_class.sierra_program.len(),
                max_contract_bytecode_size: self.config.max_contract_bytecode_size,
            });
        }

        let contract_class_object_size = serde_json::to_string(&contract_class)
            .expect("Unexpected error serializing contract class.")
            .len();
        if contract_class_object_size > self.config.max_contract_class_object_size {
            return Err(StatelessTransactionValidatorError::ContractClassObjectSizeTooLarge {
                contract_class_object_size,
                max_contract_class_object_size: self.config.max_contract_class_object_size,
            });
        }

        Ok(())
    }
```

**File:** crates/apollo_gateway_config/src/config.rs (L188-203)
```rust
impl Default for StatelessTransactionValidatorConfig {
    fn default() -> Self {
        StatelessTransactionValidatorConfig {
            validate_resource_bounds: true,
            min_gas_price: 8_000_000_000,
            max_l2_gas_amount: 1_210_000_000,
            max_calldata_length: 5000,
            max_signature_length: 4000,
            max_contract_bytecode_size: 81920,
            max_contract_class_object_size: 4089446,
            min_sierra_version: VersionId::new(1, 1, 0),
            max_sierra_version: VersionId::new(1, 9, usize::MAX),
            allow_client_side_proving: true,
            max_proof_size: 480000,
        }
    }
```
