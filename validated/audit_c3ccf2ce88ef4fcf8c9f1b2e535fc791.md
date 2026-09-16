## Analog Found

### Title
Unbounded recursion over attacker-influenced `NestedIntList` bytecode-segment structure enables stack-overflow DoS during compiled-class hashing and Starknet OS re-execution - (File: `crates/starknet_api/src/contract_class/compiled_class_hash.rs`, `crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs`)

### Summary
The CVE describes `_ux_host_class_storage_media_mount()` recursing without any depth limit or cycle detection over an attacker-supplied, tree-shaped partition table, letting a malicious disk image overflow the stack. The sequencer contains the same bug class: `bytecode_hash_node()` and `create_bytecode_segment_structure_inner()` recurse over a `NestedIntList` (the CASM `bytecode_segment_lengths` field) with **no depth bound**, while the tree's shape is influenced by the declarer-supplied Sierra program's code structure. This tree is walked both when the gateway computes/validates a declared class's compiled-class hash, and again by the Starknet OS during block re-execution/proving.

### Finding Description
`bytecode_hash_node` in [1](#0-0)  recurses into every child of a `NestedIntList::Node` with no depth tracking or limit, unlike the Cairo call-recursion path elsewhere in the codebase which is explicitly guarded by a `RecursionDepthGuard`, as seen in [2](#0-1) .

This function is invoked from `HashableCompiledClass::hash()` [3](#0-2) , which is called directly on the compiled CASM produced from a submitted Sierra class immediately after compilation, on the DECLARE transaction path: [4](#0-3) .

The identical unguarded recursion pattern also exists in the Starknet OS hint implementation used during block re-execution/proving, where `create_bytecode_segment_structure_inner` recurses over the same `NestedIntList` for every compiled class touched in a block: [5](#0-4) , invoked from the Cairo-side `validate_compiled_class_facts` loop that processes every compiled class fact referenced in the block: [6](#0-5) .

By contrast, the blockifier's own analogous structure builder explicitly restricts nesting to a shallow depth via an `assert!(segmentation_depth <= 1, ...)`, acknowledging that deep nesting is unsupported/unsafe: [7](#0-6) . This assertion is absent from the `starknet_api` hashing path and the `starknet_os` hint path, so those two call sites have no protection at all.

Critically, `bytecode_segment_lengths` is not size-bounded in terms of tree depth: the only enforced limit is total bytecode size (`max_bytecode_size`, e.g. `DEFAULT_MAX_BYTECODE_SIZE`) as seen in [8](#0-7) . A chain of single-child `Node` wrappers around a single `Leaf` consumes essentially no extra bytecode felts while adding one recursion level per wrapper, so nesting depth is decoupled from the byte-size limit — the tree can be crafted (via the Sierra program's function/control-flow structure that drives the compiler's segmentation) to be arbitrarily deep within the existing size budget.

### Impact Explanation
A stack overflow in `bytecode_hash_node` during declare-time class hash computation can crash the gateway/class-manager compilation worker handling that request (denial of service against declare-tx admission). More critically, the same unguarded recursion in `create_bytecode_segment_structure_inner`, exercised by the Starknet OS during block re-execution and proof generation for every compiled class referenced by any transaction in the block, can crash the prover/re-execution pipeline. Since block proving is mandatory to finalize and confirm blocks, this can render the network unable to confirm new transactions until the offending class/logic is special-cased or patched — matching the required "network unable to confirm new transactions" impact category. Severity aligns with the CVE's Medium rating (local, requires crafted structured input, no direct fund loss but availability/liveness impact).

### Likelihood Explanation
Reaching this requires only an unprivileged actor to declare a Cairo/Sierra contract engineered (e.g., via deeply nested function/control-flow constructs) to induce the external Sierra→CASM compiler to emit a deeply nested `bytecode_segment_lengths` tree. This is deterministic and repeatable, and the resulting compiled class becomes part of state, so every subsequent OS re-execution/proving pass over a block referencing that class will re-trigger the same unguarded recursion. No special privileges, timing, or race conditions are needed — it's directly triggerable by any declare transaction.

### Recommendation
Add an explicit recursion-depth (or equivalently, tree-depth) limit when constructing/hashing `NestedIntList`-based bytecode segment structures in `bytecode_hash_node` (`crates/starknet_api/src/contract_class/compiled_class_hash.rs`) and `create_bytecode_segment_structure_inner` (`crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs`), returning a hard error instead of recursing when the limit is exceeded — consistent with the `assert!(segmentation_depth <= 1, ...)` guard already present in `NestedFeltCounts::new_inner`. Additionally, validate/reject `bytecode_segment_lengths` structures whose nesting depth exceeds a sane bound at declare-time (before hashing), and convert the recursive traversal to an iterative (worklist/stack-based) implementation to remove reliance on native call-stack depth entirely.

### Proof of Concept
1. Author a Sierra/Cairo1 contract whose control-flow/function structure is crafted (e.g., many levels of single-branch nested match/if constructs or deeply chained tiny functions) so that the trusted `cairo-lang-starknet-sierra-compile` binary emits a `CasmContractClass.bytecode_segment_lengths` value equivalent to `Node([Node([Node([... Leaf(1) ...])])])` with tens of thousands of nesting levels, while total bytecode stays within `max_bytecode_size`.
2. Submit this class via a DECLARE transaction. During compilation, `SierraCompiler::compile` calls `executable_class.hash(&HashVersion::V2)` (`crates/apollo_compile_to_casm/src/lib.rs:60-74`), which invokes `bytecode_hash_node` recursively once per nesting level, exhausting the compilation worker's stack.
3. Even if declare-time hashing is hardened by config/environment stack size, once such a class is declared and any transaction later invokes it, Starknet OS re-execution/proving will call `create_bytecode_segment_structure_inner` on the same deeply nested structure for that block, crashing the prover process and blocking block finalization.

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

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L144-149)
```rust
    fn hash(&self, hash_version: &HashVersion) -> CompiledClassHash {
        match hash_version {
            HashVersion::V1 => hash_inner::<Poseidon, EH, NL>(self),
            HashVersion::V2 => hash_inner::<Blake2Felt252, EH, NL>(self),
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo (L97-138)
```text
// Validates the compiled class facts structure and hash, using the hint variable
// `bytecode_segment_structures` - a mapping from compilied class hash to the structure.
func validate_compiled_class_facts{poseidon_ptr: PoseidonBuiltin*, range_check_ptr}(
    n_compiled_class_facts, compiled_class_facts: CompiledClassFact*, builtin_costs: felt*
) {
    if (n_compiled_class_facts == 0) {
        return ();
    }
    alloc_locals;

    let compiled_class_fact = &compiled_class_facts[0];
    let compiled_class = compiled_class_fact.compiled_class;

    validate_entry_points(
        n_entry_points=compiled_class.n_external_functions,
        entry_points=compiled_class.external_functions,
    );

    validate_entry_points(
        n_entry_points=compiled_class.n_l1_handlers, entry_points=compiled_class.l1_handlers
    );
    // Compiled classes are expected to end with a `ret` opcode followed by a pointer to the
    // builtin costs.
    assert compiled_class.bytecode_ptr[compiled_class.bytecode_length] = 0x208b7fff7fff7ffe;
    assert compiled_class.bytecode_ptr[compiled_class.bytecode_length + 1] = cast(
        builtin_costs, felt
    );

    // Calculate the compiled class hash.
    // This hint enters a new scope that contains the bytecode segment structure of the class.
    %{ EnterScopeWithBytecodeSegmentStructure %}
    let (hash) = blake_compiled_class_hash(compiled_class, full_contract=FALSE);
    %{ LoadClass %}

    assert compiled_class_fact.hash = hash;

    return validate_compiled_class_facts(
        n_compiled_class_facts=n_compiled_class_facts - 1,
        compiled_class_facts=&compiled_class_facts[1],
        builtin_costs=builtin_costs,
    );
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

**File:** crates/apollo_compile_to_casm/src/compile_test.rs (L81-109)
```rust
#[test]
fn test_max_bytecode_size() {
    let contract_class = get_test_contract();
    let expected_casm_bytecode_length = 1965;

    // Positive flow.
    let compiler = SierraToCasmCompiler::new(SierraCompilationConfig {
        max_bytecode_size: expected_casm_bytecode_length,
        max_memory_usage: DEFAULT_MAX_MEMORY_USAGE,
        max_cpu_time: DEFAULT_MAX_CPU_TIME,
        audited_libfuncs_only: false,
    });
    let casm_contract_class = compiler
        .compile(contract_class.clone())
        .expect("Failed to compile contract class. Probably an issue with the max_bytecode_size.");
    assert_eq!(casm_contract_class.bytecode.len(), expected_casm_bytecode_length);

    // Negative flow.
    let compiler = SierraToCasmCompiler::new(SierraCompilationConfig {
        max_bytecode_size: expected_casm_bytecode_length - 1,
        max_memory_usage: DEFAULT_MAX_MEMORY_USAGE,
        max_cpu_time: DEFAULT_MAX_CPU_TIME,
        audited_libfuncs_only: false,
    });
    let result = compiler.compile(contract_class);
    assert_matches!(result, Err(CompilationUtilError::CompilationError(string))
        if string.contains("Code size limit exceeded.")
    );
}
```
