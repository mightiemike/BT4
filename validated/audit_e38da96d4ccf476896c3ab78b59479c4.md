## Title
Unbounded recursive traversal of attacker-controlled `bytecode_segment_lengths` (`NestedIntList`) causes `StackOverflowError`-equivalent DoS during compiled class hash computation for a DECLARE transaction - (`crates/starknet_api/src/contract_class/compiled_class_hash.rs`)

## Summary
A DECLARE transaction's `contract_class` field is deserialized directly into `ContractClass::V1((CasmContractClass, SierraVersion))` [1](#0-0) , and `CasmContractClass` (from `cairo_lang_starknet_classes`) carries an attacker-supplied `bytecode_segment_lengths: Option<NestedIntList>` field. This nested tree is recursively walked with no depth bound whenever the compiled class hash is computed, both in gateway/transaction validation and in `blockifier`/`starknet_os`. Because a `Node` wrapper in `NestedIntList` does not need to consume any bytecode itself, an attacker can submit a tiny bytecode array wrapped in an arbitrarily deep chain of `Node([...])` structures, causing the recursive hashing/segmentation functions to blow the call stack — the same bug class described in the Grackle GraphQL advisory (small, schema-agnostic input causing unbounded recursion / `StackOverflowError`).

## Finding Description
The compiled-class-hash computation recurses once per tree node with no depth limiting:

- `bytecode_hash_node` in `starknet_api` recurses directly over `HashableNestedIntList::iter_children()` with no depth check: [2](#0-1) 
- `create_bytecode_segment_structure_inner` in `starknet_os` (used for OS re-execution/hint processing) recurses similarly, unguarded: [3](#0-2) 
- `NestedFeltCounts::new_inner` in `blockifier` also recurses on the same structure (this specific path has a `segmentation_depth <= 1` assertion for its own use, but the two hashing paths above have no such bound): [4](#0-3) 

This `NestedIntList` is not derived independently by the sequencer from a "safe" recompilation step for the hash-check path — it is taken as-is from the attacker-submitted `CasmContractClass` and used directly to compute/verify the compiled class hash:

- `ContractClass::compiled_class_hash()` calls `casm_contract_class.hash(&HashVersion::V2)` on the class as submitted: [5](#0-4) 
- `DeclareTransaction::check_compile_class_hash_v2_declaration` (executed as part of validating a submitted declare transaction) calls `casm.hash(&HashVersion::V2)` on the exact submitted `CasmContractClass`: [6](#0-5) 

Because `NestedIntList::Node(children)` does not require any leaf under it to consume bytecode, an attacker can construct a deeply nested `Node([Node([Node([... Leaf(1) ...])])])` structure with only 1 felt of actual bytecode, yielding a payload whose size is roughly linear in nesting depth but whose recursion depth (and thus native stack usage) is exactly equal to that nesting depth — i.e., a small, cheap-to-construct DECLARE transaction can drive arbitrarily deep recursion.

## Impact Explanation
Hitting a native Rust stack overflow aborts the process (Rust does not allow catching stack overflow), so any sequencer/full-node component that deserializes and hashes this class (gateway validation, mempool/batcher during declare processing, or the Starknet OS during block re-execution) crashes. Since any unprivileged account can submit a DECLARE transaction, this is a direct network-wide denial-of-service path: nodes crash while validating or executing a single malicious transaction, potentially halting block production/confirmation (a network unable to confirm new transactions), matching the CWE-400/DoS impact of the cited Grackle advisory.

## Likelihood Explanation
Likelihood is high: constructing the malicious `bytecode_segment_lengths` value requires no special knowledge of any specific contract's Sierra/Cairo logic (mirroring the advisory's note that "no specific knowledge of an application's schema is required") — it is a purely structural, attacker-chosen JSON/binary nesting of an already-public, externally-visible field of the `CasmContractClass` format. No fee-based deterrent stops the initial parse/hash step, since the crash occurs while validating the class, before/instead of normal fee-charged execution.

## Recommendation
Enforce and validate a maximum nesting depth for `bytecode_segment_lengths` immediately after deserializing a submitted `CasmContractClass` (reject with a normal validation error rather than recursing), and/or rewrite `bytecode_hash_node`, `create_bytecode_segment_structure_inner`, and `NestedFeltCounts::new_inner` to use an explicit iterative worklist/stack instead of native recursion so pathological nesting cannot exhaust the call stack. Add a fuzz/unit test asserting these functions handle deeply nested (e.g., depth > 10,000) `NestedIntList` inputs without crashing.

## Proof of Concept
1. Craft a `CasmContractClass` JSON with `bytecode: [<one felt>]` and `bytecode_segment_lengths` set to a chain of nested `{"Node": [...]}` wrapping a single `{"Leaf": 1}`, nested to a large depth (e.g., 100,000+ levels) — a payload only a few hundred KB in size.
2. Submit this as the `contract_class` of a DECLARE (v2/v3) transaction to the gateway/mempool.
3. During validation, `DeclareTransaction::check_compile_class_hash_v2_declaration` → `ContractClass::compiled_class_hash` → `CasmContractClass::hash` → `bytecode_hash` → `bytecode_hash_node` recurses once per nesting level with no depth guard, exhausting the thread's call stack and aborting the process handling the request.

### Citations

**File:** crates/starknet_api/src/contract_class/structs.rs (L45-61)
```rust
/// Represents a raw Starknet contract class.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, derive_more::From)]
#[allow(clippy::large_enum_variant)]
pub enum ContractClass {
    V0(DeprecatedContractClass),
    V1(VersionedCasm),
}

impl ContractClass {
    pub fn compiled_class_hash(&self) -> CompiledClassHash {
        match self {
            ContractClass::V0(_) => panic!("Cairo 0 doesn't have compiled class hash."),
            ContractClass::V1((casm_contract_class, _sierra_version)) => {
                casm_contract_class.hash(&HashVersion::V2)
            }
        }
    }
```

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
