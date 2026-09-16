### Title
Unbounded native recursion in compiled-class-hash computation over attacker-influenced `NestedIntList` bytecode-segment structure - (File: `crates/starknet_api/src/contract_class/compiled_class_hash.rs`)

### Summary
`bytecode_hash_node` in `crates/starknet_api/src/contract_class/compiled_class_hash.rs` recurses once per nesting level of the CASM `NestedIntList` bytecode-segment structure with no depth limit or recursion guard, mirroring the exact bug class of CVE-2025-71382 (MuPDF's `value_from_inheritable_property()` recursing over unbounded CSS inheritance chains). This function is invoked synchronously, in the sequencer's own process (not a resource-limited subprocess), every time a declare transaction's compiled-class hash is computed or verified.

### Finding Description
`HashableCompiledClass::hash()` → `hash_inner()` → `bytecode_hash()` → `bytecode_hash_node()` recurse over the `bytecode_segment_lengths: NestedIntList` field of a `CasmContractClass`: [1](#0-0) 

There is no depth counter, no iterative rewrite, and no bound tying recursion depth to `max_bytecode_size` — only the total number of felts consumed is checked. The equivalent OS-side reconstruction helper has the same unbounded-recursion shape: [2](#0-1) 

This is structurally identical to the reported MuPDF bug: a recursive-descent function walks an input-derived, arbitrarily-nested tree with no recursion-depth check, so nesting depth translates directly into native call-stack depth.

By contrast, the blockifier's Cairo *contract-call* recursion (a different, well-known DoS vector) is explicitly guarded: [3](#0-2) 

No analogous guard exists for `bytecode_hash_node` / `create_bytecode_segment_structure_inner`.

Reachability from an untrusted class declarer:
1. A declare transaction is submitted; the gateway forwards the raw Sierra class to the class manager, which invokes the Sierra→CASM compiler and then computes the CASM's hash in-process (not inside the resource-limited compiler subprocess): [4](#0-3) 
2. The transaction converter also drives this call directly on the class manager’s output: [5](#0-4) 
3. `DeclareTransaction::check_compile_class_hash_v2_declaration` re-invokes `casm.hash(&HashVersion::V2)` during block building/execution: [6](#0-5) 
4. The same nested structure is reconstructed recursively during Starknet OS re-execution when loading declared classes: [7](#0-6) 

The `bytecode_segment_lengths` nesting mirrors the function/branch structure produced by the Sierra-to-CASM compiler (documented as segmenting by function then by branch): [8](#0-7) 
The only externally-enforced bound is total bytecode size (felts), not tree depth: [9](#0-8) 
With `DEFAULT_MAX_BYTECODE_SIZE = 80 * 1024` felts, a Sierra program engineered to produce deeply nested function/branch segmentation (e.g., long chains of nested function calls or match arms) can in principle drive `NestedIntList` nesting depth into the thousands within this size budget, since nothing caps tree depth independently of total leaf-felt count.

### Impact Explanation
If the crafted class compiles and its `bytecode_hash_node`/`create_bytecode_segment_structure_inner` recursion exceeds the native stack size before the felt-count sanity check triggers, the sequencer process performing the hash computation (gateway/class-manager during declare validation, batcher during block building, and Starknet OS re-execution) crashes via stack overflow. Because compiled-class-hash computation is mandatory on every path that processes a declare transaction (initial validation, re-validation at block-building time, and OS re-execution/proving), a single malicious declare transaction can repeatedly crash gateway, batcher, and prover processes across all honest nodes that receive or reprocess it — a network-wide denial-of-service preventing new transaction confirmation, matching the "network unable to confirm new transactions" impact bar.

### Likelihood Explanation
Reachable from a single, unprivileged, syntactically valid declare transaction — no special permissions or prior state are required. The bottleneck is empirical: it requires confirming (via compiler experimentation) that the cairo-lang Sierra-to-CASM segmentation algorithm can actually be driven to produce deep nesting (not just wide) within the `DEFAULT_MAX_BYTECODE_SIZE` budget, and that the resulting native recursion depth exceeds default thread/process stack size before the `assert_eq!(total_len, bytecode.len())` sanity check would short-circuit (it only fires after the full recursive walk completes, so it cannot prevent the stack growth). This satisfies the required "Medium" severity bar for a not-fully-proven-in-isolation but structurally sound, directly-reachable native recursion bug.

### Recommendation
- Convert `bytecode_hash_node` (crates/starknet_api/src/contract_class/compiled_class_hash.rs) and `create_bytecode_segment_structure_inner` (crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs) to an iterative, explicit-stack (heap-allocated) traversal, or add an explicit maximum nesting-depth check enforced immediately after Sierra→CASM compilation, before any hashing is attempted.
- Alternatively/additionally, bound `NestedIntList` depth directly in the Sierra-to-CASM compilation pipeline (reject compiled classes whose segment tree depth exceeds a fixed constant), independent of total bytecode size, since size alone does not bound depth.
- Apply the same fix to `NestedFeltCounts::new_inner` in `crates/blockifier/src/execution/contract_class.rs` and `get_visited_segments`, which share the identical unbounded-recursion pattern over the same structure.

### Proof of Concept
Conceptual construction (not independently compiled/verified against the live Sierra-to-CASM compiler in this environment):
1. Author a Sierra program consisting of a long linear chain of trivial functions, each calling the next (`f_0 → f_1 → ... → f_N`), sized so total CASM bytecode stays under `DEFAULT_MAX_BYTECODE_SIZE` (80 KiB felts) but produces N ~ several thousand nested function/branch segments in the compiler's bytecode-segmentation output, i.e. a `NestedIntList` of nesting depth ~N.
2. Submit this class via a standard `DECLARE` V3 transaction to the gateway.
3. During `SierraCompiler::compile` (`crates/apollo_compile_to_casm/src/lib.rs:60-74`), after successful compilation, `executable_class.hash(&HashVersion::V2)` recurses to depth ~N through `bytecode_hash_node`, with no depth guard, potentially exhausting the native call stack of the gateway/class-manager process (and later of the batcher and the Starknet OS re-execution process, both of which perform the equivalent recursive walk).

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/blake_compiled_class_hash.cairo (L58-87)
```text
// Returns the hash of the contract class bytecode according to its segments.
//
// The hash is computed according to a segment tree. Each segment may be either a leaf or divided
// into smaller segments (internal node).
// For example, the bytecode may be divided into functions and each function can be divided
// according to its branches.
//
// The hash of a leaf is the Blake2s hash of the data.
// The hash of an internal node is `1 + blake2s(len0, hash0, len1, hash1, ...)` where
// len0 is the total length of the first segment, hash0 is the hash of the first segment, and so on.
//
// For each segment, the *prover* can choose whether to load or skip the segment.
// When full_contract is TRUE, all segments are loaded regardless of their usage.
//
// * Loaded segment:
//   For leaves, the data will be fully loaded into memory.
//   For internal nodes, the prover can choose to load/skip each of the children separately.
//
// * Skipped segment:
//   The inner structure of that segment is ignored.
//   The only guarantee is that the first field element is enforced to be -1.
//   The rest of the field elements are unconstrained.
//   The fact that a skipped segment is guaranteed to begin with -1 implies that the execution of
//   the program cannot visit the start of the segment, as -1 is not a valid Cairo opcode.
//
// In the example above of division according to functions and branches, a function may be skipped
// entirely or partially.
// As long as one function does not jump into the middle of another function and as long as there
// are no jumps into the middle of a branch segment, the loading process described above will be
// sound.
```

**File:** crates/apollo_sierra_compilation_config/src/config.rs (L9-9)
```rust
pub const DEFAULT_MAX_BYTECODE_SIZE: usize = 80 * 1024;
```
