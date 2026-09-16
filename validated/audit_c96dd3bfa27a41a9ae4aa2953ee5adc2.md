## Title
Uncontrolled Recursion in Compiled Class Hash Computation via Declare Transaction - (File: `crates/starknet_api/src/contract_class/compiled_class_hash.rs`)

### Summary
The compiled-class-hash algorithm recursively walks the `NestedIntList` bytecode-segmentation tree produced by Sierra→CASM compilation with no depth limit. This tree's structure is influenced by the Sierra program submitted in a `Declare` transaction. A crafted class can force a deeply-nested segmentation tree, and hashing it (which happens in-process, on the sequencer's/class-manager's hot path for every declared class) can exhaust the call stack, analogous to Squid's uncontrolled recursion on the `X-Forwarded-For` header (CVE-2023-50269): a single unprivileged input drives unbounded recursive descent with no depth guard.

### Finding Description
`bytecode_hash_node` recurses once per nesting level of the `NestedIntList` describing bytecode segments, with no depth check: [1](#0-0) 

This is invoked from `HashableCompiledClass::hash` (via `bytecode_hash`), which is called directly, in-process, right after Sierra→CASM compilation to compute `executable_class_hash_v2`: [2](#0-1) 

and again in `ClassManager::add_class`, on the class-declaration path reachable by any account submitting a `Declare` transaction: [3](#0-2) 

The identical unbounded-recursion pattern also exists in the Starknet OS hint used during block re-execution/proving: [4](#0-3) 

By contrast, other recursive call paths in the codebase that are reachable from transaction execution are explicitly protected by a `RecursionDepthGuard` (`max_recursion_depth`, default 50) that errors out before the stack is exhausted: [5](#0-4) 

No equivalent guard exists for the bytecode-segment tree walked by `bytecode_hash_node` / `create_bytecode_segment_structure_inner`. The `bytecode_segment_lengths` field comes from the compiler's own segmentation of the submitted Sierra program (mirroring its function/branch structure); a declarer can shape a Sierra program (within the existing `max_contract_bytecode_size` / `max_contract_class_object_size` limits, e.g. ~80 KB / ~4 MB) to produce a segmentation tree with very deep nesting (deeply nested branches/functions), since neither the compiler output format nor the hashing code enforces a maximum nesting depth.

### Impact Explanation
If the nesting depth is large enough, recursive traversal (`bytecode_hash_node`/`create_bytecode_segment_structure_inner`) can overflow the calling thread's stack, crashing the process performing the computation. Since this computation happens in-process for every `Declare` transaction on the class-manager/compiler path (not sandboxed like the actual Sierra→CASM compiler subprocess), a crafted single `Declare` transaction can crash the class manager component on every honest node that processes it — a network-wide denial of service preventing confirmation of new transactions, matching the "network unable to confirm new transactions" impact bar.

### Likelihood Explanation
Likelihood is high: the attack requires only a single, unprivileged `Declare` transaction with a specially crafted Sierra program; no special permissions, staking, or node compromise is needed. It is reachable purely through the normal declare-transaction path that every sequencer/class-manager instance must process.

### Recommendation
Add an explicit depth bound (or convert to an iterative/worklist algorithm) in `bytecode_hash_node` (`compiled_class_hash.rs`) and in `create_bytecode_segment_structure_inner` (`starknet_os/.../compiled_class/utils.rs`), mirroring the `assert!(segmentation_depth <= 1, ...)` guard already present in `NestedFeltCounts::new_inner` (`blockifier/src/execution/contract_class.rs`). Reject classes whose segment structure exceeds the bound during compilation/validation, before the untrusted structure is ever recursively hashed in-process.

### Proof of Concept
1. Craft (or generate via the audited Cairo compiler by writing deeply nested `if`/`match` branches or deeply nested function calls) a Sierra program that compiles to a `CasmContractClass` whose `bytecode_segment_lengths` is a `NestedIntList::Node` tree with thousands of nesting levels, while staying within `max_contract_bytecode_size`/`max_contract_class_object_size`.
2. Submit this class via a `Declare` transaction to the gateway.
3. Observe that when `ClassManager::add_class` / `SierraCompiler::compile` computes `executable_class.hash(&HashVersion::V2)` in-process [6](#0-5) , the recursive call chain in `bytecode_hash_node` [1](#0-0)  exhausts the stack and crashes the process handling class declarations.

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

**File:** crates/apollo_class_manager/src/class_manager.rs (L70-90)
```rust
    #[instrument(skip(self, class), ret, err)]
    pub async fn add_class(&mut self, class: RawClass) -> ClassManagerResult<ClassHashes> {
        let sierra_class = SierraContractClass::try_from(&class)?;
        let class_hash = sierra_class.calculate_class_hash();
        if let Ok(Some(executable_class_hash_v2)) =
            self.classes.get_executable_class_hash_v2(class_hash)
        {
            // Class already exists.
            return Ok(ClassHashes { class_hash, executable_class_hash_v2 });
        }

        let compilation_start_time = Instant::now();
        let (raw_executable_class, executable_class_hash_v2) =
            self.compiler.compile(class.clone()).await.map_err(|err| match err {
                SierraCompilerClientError::SierraCompilerError(error) => {
                    ClassManagerError::SierraCompiler { class_hash, error }
                }
                SierraCompilerClientError::ClientError(error) => {
                    ClassManagerError::Client(error.to_string())
                }
            })?;
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
