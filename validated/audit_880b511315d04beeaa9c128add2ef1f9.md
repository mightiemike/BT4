### Title
Unbounded recursion in CASM bytecode-segment hashing causes sequencer stack overflow on declare - ([File: crates/starknet_api/src/contract_class/compiled_class_hash.rs])

### Summary
The CVE describes a stack-overflow DoS in `_rsvg_css_normalize_font_size` caused by unbounded recursion over attacker-controlled nested/circular definitions. The sequencer has an analogous unguarded-recursion pattern: `bytecode_hash_node` in `crates/starknet_api/src/contract_class/compiled_class_hash.rs` recurses over a `NestedIntList`/`HashableNestedIntList` structure (`bytecode_segment_lengths`) with no depth limit, and is invoked on every Declare transaction whose class carries a Cairo-1 `CasmContractClass`.

### Finding Description
`CompiledClassV1`/`CasmContractClass::hash()` calls `hash_inner` → `bytecode_hash` → `bytecode_hash_node`: [1](#0-0) 

`bytecode_hash_node` recurses once per nesting level of the `NestedIntList`/`bytecode_segment_lengths` structure with no maximum-depth check, unlike other consumers of the same type. For comparison, the equivalent structure-builder in the blockifier explicitly asserts a depth bound (`segmentation_depth <= 1`): [2](#0-1) 

but `bytecode_hash_node` (used for hash computation) and the Starknet-OS equivalents `create_bytecode_segment_structure_inner` and `BytecodeSegmentNode::hash` have no such guard: [3](#0-2) 

This hashing path is reached on the transaction path via `check_compile_class_hash_v2_declaration`, which is executed for every V3 declare transaction (when `block_casm_hash_v1_declares` is enabled) inside blockifier `run_execute`: [4](#0-3) [5](#0-4) 

It is also reached during Starknet-OS re-execution of a declare, via the `load_class`/`set_ap_to_segment_hash` hint that calls `BytecodeSegmentNode::hash::<H>()`: [6](#0-5) 

The `bytecode_segment_lengths` structure of the declared `CasmContractClass` derives from the compiled Sierra program's call-graph segmentation (produced when the class's Sierra program is compiled to CASM, either client-side or, in the sequencer's own compile path, via `apollo_compile_to_casm`). A contract with many deeply/mutually-nested function definitions can force the compiler to emit a deeply nested `NestedIntList::Node` chain, which then drives unbounded native-stack recursion when the sequencer subsequently hashes/validates the class.

### Impact Explanation
A sufficiently deep `NestedIntList` nesting drives `bytecode_hash_node` (and the analogous OS/hint functions) into native stack exhaustion. In Rust, stack overflow from unbounded recursion aborts the process (it is not a catchable `Result`/panic), so this crashes the sequencer process handling the declare transaction — during gateway/mempool validation and/or block execution and OS re-execution. Because this is triggered by a single Declare transaction reachable from any unprivileged class declarer, a crash here can halt block production/validation, i.e., "a network unable to confirm new transactions," matching the required impact bar.

### Likelihood Explanation
Reaching this path only requires submitting one Declare (V2/V3) transaction for a Cairo-1 class whose compiled CASM produces a deeply nested bytecode segmentation structure — no special privileges, staking, or timing needed. The main uncertainty (not fully verified with the available tools) is exactly how deep the segmentation nesting can practically be driven by adversarial Sierra source given the sequencer's own Sierra→CASM compiler (`apollo_compile_to_casm`); if the compiler's segmentation is effectively bounded by shallow call-graph SCC structure, achievable recursion depth may be limited. This uncertainty should be validated with an end-to-end compile of an adversarially constructed contract.

### Recommendation
Add explicit depth limits (mirroring the `segmentation_depth <= 1` assertion already used in `NestedFeltCounts::new_inner`) to `bytecode_hash_node`, `create_bytecode_segment_structure_inner`, and `BytecodeSegmentNode::hash`, rejecting/erroring out classes whose `bytecode_segment_lengths` exceed the expected nesting depth, before recursive hashing/structure-building is performed on any transaction-supplied class.

### Proof of Concept
Not independently executed; conceptual PoC: craft (or directly construct, bypassing normal compilation, if the wire format allows submitting `bytecode_segment_lengths` directly) a `CasmContractClass` whose `bytecode_segment_lengths` is a deeply nested chain of `NestedIntList::Node([NestedIntList::Node([...])])` (e.g., depth on the order of 10^5), then submit it via a Declare V2/V3 transaction. On processing (`check_compile_class_hash_v2_declaration` / block execution / OS re-execution), `bytecode_hash_node` recurses to that depth and overflows the native stack, aborting the sequencer process.

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

**File:** crates/blockifier/src/execution/contract_class.rs (L162-168)
```rust
    /// Recursively builds the nested structure and returns it with the number of items consumed.
    fn new_inner(
        bytecode_segment_lengths: &NestedIntList,
        bytecode: &[BigUintAsHex],
        segmentation_depth: usize,
    ) -> (Self, usize) {
        assert!(segmentation_depth <= 1, "Only supported for segmentation depth at most 1.");
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

**File:** crates/starknet_api/src/executable_transaction.rs (L226-244)
```rust
    /// Verifies that the compiled class hash field in the declare tx,
    /// is compiled_class_hash_v2 of the compiled contract.
    pub fn check_compile_class_hash_v2_declaration(&self) -> Result<(), StarknetApiError> {
        let compiled_class = &self.class_info.contract_class;
        let compiled_class_hash_v2 = match &compiled_class {
            ContractClass::V0(_) => return Ok(()),
            ContractClass::V1((casm, _)) => casm.hash(&HashVersion::V2),
        };
        let compiled_class_hash = self.compiled_class_hash();
        if compiled_class_hash_v2 != compiled_class_hash {
            let err_var = CasmHashMismatch {
                hash: self.class_hash(),
                actual: compiled_class_hash,
                expected: compiled_class_hash_v2,
            };
            return Err(StarknetApiError::DeclareTransactionCasmHashMissMatch(Box::new(err_var)));
        }
        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/transactions.rs (L176-190)
```rust
            starknet_api::transaction::DeclareTransaction::V2(DeclareTransactionV2 {
                compiled_class_hash,
                ..
            })
            | starknet_api::transaction::DeclareTransaction::V3(DeclareTransactionV3 {
                compiled_class_hash,
                ..
            }) => {
                if context.tx_context.block_context.versioned_constants.block_casm_hash_v1_declares
                    && self.version() >= TransactionVersion::THREE
                {
                    self.check_compile_class_hash_v2_declaration()?
                }
                try_declare(self, state, class_hash, Some(*compiled_class_hash))?
            }
```

**File:** crates/starknet_os/src/hints/hint_implementation/compiled_class/implementation.rs (L161-166)
```rust
pub(crate) fn set_ap_to_segment_hash<H: HashFunction>(ctx: HintContext<'_>) -> OsHintResult {
    let bytecode_segment_structure: &BytecodeSegmentNode =
        ctx.exec_scopes.get_ref(Scope::BytecodeSegmentStructure.into())?;

    Ok(insert_value_into_ap(ctx.vm, bytecode_segment_structure.hash::<H>())?)
}
```
