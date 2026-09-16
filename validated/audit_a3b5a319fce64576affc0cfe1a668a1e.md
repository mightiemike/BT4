### Title
Uncontrolled recursion in `create_bytecode_segment_structure_inner` causes stack-overflow during Starknet OS re-execution of a maliciously nested declared class - (File: `crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs`)

### Summary
The Starknet OS hint that reconstructs a contract's bytecode-segment tree recurses once per nesting level of `NestedIntList` (`bytecode_segment_lengths`) with no depth bound, mirroring the CVE-2018-5772 pattern (uncontrolled recursion over an attacker-influenced nested structure causing a native stack overflow / process crash).

### Finding Description
`create_bytecode_segment_structure_inner` walks a `NestedIntList` tree derived from a declared class's CASM (`bytecode_segment_lengths`) and recurses into every `NestedIntList::Node` child with no depth guard: [1](#0-0) 

This is invoked from the hint `load_classes_and_create_bytecode_segment_structures`, which is executed during Starknet OS re-execution (SNOS) for every Cairo-1 class touched by a block: [2](#0-1) 

The `bytecode_segment_lengths` value is produced by the Sierra→CASM compiler from the shape of the (attacker-controlled) Sierra program submitted in a `declare` transaction, and there is no explicit bound in the sequencer code on the *nesting depth* of the resulting `NestedIntList` — only overall byte-code size is bounded (`max_bytecode_size` / `max_contract_bytecode_size`), which does not limit tree depth, since a contract can be structured (e.g. via deeply nested function/branch segmentation) to produce a `NestedIntList::Node` chain many levels deep while staying within the size limit: [3](#0-2) [4](#0-3) 

Note that a sibling code path, `NestedFeltCounts::new_inner`, explicitly guards against deep nesting with `assert!(segmentation_depth <= 1, ...)`: [5](#0-4) 
but `create_bytecode_segment_structure_inner` used by the OS hint implementation has no equivalent guard, showing the depth-limiting pattern is known elsewhere in the codebase but missing here.

### Impact Explanation
If a class with a sufficiently deep `bytecode_segment_lengths` tree is declared and executed on-chain, every sequencer/full node running Starknet-OS re-execution over that block will recurse `create_bytecode_segment_structure_inner` to the same depth. A sufficiently deep tree can exhaust the native thread stack, crashing the OS-execution process for that block on every honest node simultaneously — a network-wide denial of block confirmation / re-execution (an outage of the OS re-execution path used for proving and node syncing), matching the "network unable to confirm new transactions" impact bar.

### Likelihood Explanation
Reachable purely via a normal `declare` transaction from an unprivileged account; there is no special privilege required. The class must pass stateless gateway validation (Sierra version, bytecode size, entry point sorting) but none of these validations bound the nesting depth of the compiled `bytecode_segment_lengths` structure, so a crafted contract that produces deeply nested segments would pass existing checks.

### Recommendation
Add an explicit maximum recursion/nesting-depth check in `create_bytecode_segment_structure_inner` (and reject/error out via `OsHintError` rather than recursing unbounded), or convert the traversal to an iterative (stack-based) algorithm. Additionally consider validating `bytecode_segment_lengths` nesting depth at declare-time in `StatelessTransactionValidator`/`SierraCompiler` so malformed/adversarial segment trees are rejected before being persisted and later re-executed.

### Proof of Concept
1. Craft a Sierra program whose function/branch structure causes the Sierra→CASM compiler to emit a `bytecode_segment_lengths: NestedIntList` consisting of a long chain of nested `Node([Node([Node([...Leaf(n)...])])])` (depth proportional to, e.g., tens of thousands, while total bytecode stays under `max_contract_bytecode_size`).
2. Submit this class via a normal `declare` transaction; it passes `StatelessTransactionValidator::validate_declare_tx` since only overall size/version/entry-point sort order are checked.
3. Once the class is declared and invoked (or simply present in a block requiring OS re-execution), the Starknet OS run for that block calls `load_classes_and_create_bytecode_segment_structures` → `create_bytecode_segment_structure` → `create_bytecode_segment_structure_inner`, recursing to the crafted depth and overflowing the native stack, crashing the process performing OS re-execution.

### Citations

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

**File:** crates/starknet_os/src/hints/hint_implementation/compiled_class/implementation.rs (L168-217)
```rust
// Hint extensions.
pub(crate) fn load_classes_and_create_bytecode_segment_structures<S: StateReader>(
    hint_processor: &mut SnosHintProcessor<'_, S>,
    mut ctx: HintContext<'_>,
) -> OsHintExtensionResult {
    let identifier_getter = ctx.program;
    let mut hint_extension = HintExtension::new();
    let mut compiled_class_facts_ptr = ctx.vm.add_memory_segment();
    let mut bytecode_segment_structures = BTreeMap::new();
    // Insert n_compiled_class_facts, compiled_class_facts.
    ctx.insert_value(Ids::CompiledClassFacts, compiled_class_facts_ptr)?;
    ctx.insert_value(Ids::NCompiledClassFacts, hint_processor.compiled_classes.len())?;
    // Iterate only over cairo 1 classes.
    for (compiled_class_hash, compiled_class) in hint_processor.compiled_classes.iter() {
        let compiled_class_fact = CompiledClassFact { compiled_class_hash, compiled_class };
        compiled_class_fact.load_into(
            ctx.vm,
            identifier_getter,
            compiled_class_facts_ptr,
            &ctx.program.constants,
        )?;

        // Compiled classes are expected to end with a `ret` opcode followed by a pointer to
        // the builtin costs.
        let bytecode_ptr_address = get_address_of_nested_fields_from_base_address(
            compiled_class_facts_ptr,
            CairoStruct::CompiledClassFact,
            ctx.vm,
            &["compiled_class", "bytecode_ptr"],
            identifier_getter,
        )?;
        let bytecode_ptr = ctx.vm.get_relocatable(bytecode_ptr_address)?;
        let builtin_costs = ctx.get_ptr(Ids::BuiltinCosts)?;
        let encoded_ret_opcode = 0x208b7fff7fff7ffe;
        let data = [encoded_ret_opcode.into(), builtin_costs.into()];
        ctx.vm.load_data((bytecode_ptr + compiled_class.bytecode.len())?, &data)?;

        // Extend hints.
        for (rel_pc, hints) in compiled_class.hints.iter() {
            let abs_pc = Relocatable::from((bytecode_ptr.segment_index, *rel_pc));
            hint_extension.insert(abs_pc, hints.iter().map(|h| any_box!(h.clone())).collect());
        }

        bytecode_segment_structures.insert(
            *compiled_class_hash,
            create_bytecode_segment_structure(
                &compiled_class.bytecode.iter().map(|x| Felt::from(&x.value)).collect::<Vec<_>>(),
                compiled_class.get_bytecode_segment_lengths(),
            )?,
        );
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

**File:** crates/apollo_gateway_config/src/config.rs (L188-204)
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
}
```

**File:** crates/blockifier/src/execution/contract_class.rs (L163-194)
```rust
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
