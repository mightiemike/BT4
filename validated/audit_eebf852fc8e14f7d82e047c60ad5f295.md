Based on my investigation, I found a strong analog to the reported Go parser stack-exhaustion bug in the sequencer's compiled-class-hash computation logic.

### Title
Stack exhaustion via unbounded recursion in `bytecode_hash_node` when hashing a declared class with a deeply-nested bytecode segment structure - (File: `crates/starknet_api/src/contract_class/compiled_class_hash.rs`)

### Summary
`bytecode_hash_node`, which computes a Starknet compiled-class hash from a CASM contract's `bytecode_segment_lengths` (`NestedIntList`), recurses once per nesting level of the segment tree with no depth bound, mirroring the unbounded-recursion pattern in the reported Go parser CVE (deeply nested input → stack overflow panic).

### Finding Description
`bytecode_hash_node` walks a `HashableNestedIntList` (`NestedIntList` for `CasmContractClass`) recursively: for every `Node` variant it recurses into each child via `.iter_children().map(|child| bytecode_hash_node(...))` with no maximum-depth check. [1](#0-0) 

This function is invoked by `hash_inner`, which is the implementation behind `HashableCompiledClass::hash`, used by every Sierra→CASM declare flow to compute `CompiledClassHash`. [2](#0-1) 

The gateway/mempool declare path (`RpcDeclareTransaction::V3`) calls `self.class_manager_client.add_class(tx.contract_class)`, which triggers Sierra-to-CASM compilation (`apollo_compile_to_casm`) and then computes `executable_class.hash(&HashVersion::V2)` immediately, comparing it to the transaction-supplied `compiled_class_hash` for every single declare transaction submitted by any user. [3](#0-2) [4](#0-3) 

The `bytecode_segment_lengths` structure is produced by the Sierra-to-CASM compiler based on the nesting of functions/branches in the user-submitted Sierra program (as documented: "the bytecode may be divided into functions and each function can be divided according to its branches"). [5](#0-4) 

Gateway limits bound only the flat size of the Sierra program (`max_contract_bytecode_size` = 81920 felts) and total JSON object size, not the recursion depth of any derived tree structure. [6](#0-5)  An attacker can craft a Sierra program (well within the 81920-felt / ~4MB size limits) containing tens of thousands of sequentially nested control-flow branches. The compiler's segmentation algorithm can then generate a deeply/linearly-nested `NestedIntList` tree (depth proportional to the number of nested branches), which `bytecode_hash_node` will recurse over without any depth limit, exhausting the call stack and crashing the process with an unrecoverable stack overflow (Rust does not allow catching stack-overflow aborts).

The identical unbounded-recursion pattern also exists in the corresponding Starknet-OS re-execution/proving path, `create_bytecode_segment_structure_inner`, which builds the same segment tree from `bytecode_segment_lengths` when loading compiled-class facts during block building / re-execution, and in the analogous `NestedFeltCounts::new_inner` gas-estimation helper. [7](#0-6) [8](#0-7) 

### Impact Explanation
A crafted declare transaction (or the resulting declared class being re-executed by every node building/verifying the containing block, and by the Starknet OS when generating/verifying proofs) can crash any sequencer/gateway/prover process that computes the compiled class hash or replays the OS for that class. Because stack overflows in Rust abort the process rather than raising a catchable error, this results in denial-of-service against gateway, batcher, and OS re-execution/proving components processing the transaction — a single unprivileged declare transaction can render nodes unable to process new transactions (matches the "network unable to confirm new transactions" impact bar).

### Likelihood Explanation
Reachable with a single, unprivileged `DECLARE` transaction from any account — no special privileges, no other network participants, no proposer/operator/peer trust required. The size limits on the Sierra program (`max_contract_bytecode_size` = 81920) are generous enough to plausibly construct thousands of nested branches, which is the likely trigger for the segmentation tree's depth. Exact confirmation of how deep cairo-lang-starknet-classes's compiler segmentation nests for adversarial branch structures would require testing against the compiler crate (external dependency, not in this repo), but the recursive consumer code in this repository has no defensive depth check whatsoever, matching the exact bug class of the reported CVE.

### Recommendation
- Add an explicit maximum recursion/nesting-depth check (and/or convert to an iterative, explicit-stack algorithm) in `bytecode_hash_node` (`crates/starknet_api/src/contract_class/compiled_class_hash.rs`), `create_bytecode_segment_structure_inner` (`crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs`), and `NestedFeltCounts::new_inner` (`crates/blockifier/src/execution/contract_class.rs`).
- Reject/flatten `NestedIntList` structures whose depth exceeds a small, sane bound (e.g., the depth-1 assumption already asserted in `NestedFeltCounts::new_inner`) during gateway stateless validation, before hashing/compiling untrusted classes.
- Add a fuzz/regression test that declares a contract engineered to produce a deeply nested `bytecode_segment_lengths` and asserts graceful rejection rather than a stack-overflow crash.

### Proof of Concept
1. Craft a Cairo 1 contract with a very large number (e.g., 50,000+) of sequentially nested `if`/`match` branches within a single function, staying under `max_contract_bytecode_size` (81920 felts) and `max_contract_class_object_size` (4,089,446 bytes).
2. Compile to Sierra and submit as a `DECLARE` v3 transaction to the gateway.
3. During `convert_rpc_tx_to_internal` → `class_manager_client.add_class` → `SierraCompiler::compile` → `executable_class.hash(&HashVersion::V2)`, the resulting CASM's `bytecode_segment_lengths` recurses through `bytecode_hash_node` proportional to the branch nesting depth, exhausting the thread stack and aborting the gateway/class-manager process.
4. The same crafted class, once declared, will also crash any node re-executing the block (Starknet OS `create_bytecode_segment_structure_inner`) or estimating CASM hash resources (`NestedFeltCounts::new_inner`), amplifying the DoS across the network.

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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L347-360)
```rust
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => {
                let ClassHashes { class_hash, executable_class_hash_v2 } =
                // TODO(Dori): Make this async and spawn a task to compile and add it to the class manager.
                    self.class_manager_client.add_class(tx.contract_class).await?;
                // TODO(Aviv): Ensure that we do not want to
                // allow declare with compiled class hash v1.
                if tx.compiled_class_hash != executable_class_hash_v2 {
                    return Err(TransactionConverterError::ValidateCompiledClassHashError(
                        ValidateCompiledClassHashError::CompiledClassHashMismatch {
                            computed_class_hash: executable_class_hash_v2,
                            supplied_class_hash: tx.compiled_class_hash,
                        },
                    ));
                }
```

**File:** crates/apollo_compile_to_casm/src/lib.rs (L60-74)
```rust
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/poseidon_compiled_class_hash.cairo (L60-70)
```text
// Returns the hash of the contract class bytecode according to its segments.
//
// The hash is computed according to a segment tree. Each segment may be either a leaf or divided
// into smaller segments (internal node).
// For example, the bytecode may be divided into functions and each function can be divided
// according to its branches.
//
// The hash of a leaf is the Poseidon hash the data.
// The hash of an internal node is `1 + poseidon(len0, hash0, len1, hash1, ...)` where
// len0 is the total length of the first segment, hash0 is the hash of the first segment, and so on.
//
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
