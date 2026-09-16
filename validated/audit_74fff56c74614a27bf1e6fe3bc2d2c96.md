### Title
Unbounded recursion in `bytecode_hash_node` during compiled-class-hash verification can cause native stack exhaustion on declare - (File: crates/starknet_api/src/contract_class/compiled_class_hash.rs)

### Summary
`bytecode_hash_node` in `crates/starknet_api/src/contract_class/compiled_class_hash.rs` recursively walks the `NestedIntList` bytecode-segment structure of a `CasmContractClass` to compute the Blake2/Poseidon compiled-class hash, exactly the recursive-tree-traversal pattern flagged in the external report. [1](#0-0)  This is native Rust recursion (unlike Cairo VM recursion, which is metered by step/gas limits and protected by `RecursionDepthGuard`), so its depth is bounded only by the shape of `bytecode_segment_lengths` carried in the CASM produced for a declared class. [2](#0-1) 

### Finding Description
`bytecode_hash_node` recurses once per level of nesting in the `NestedIntList` segment structure: for a `Node`, it maps `iter_children` and recursively calls itself for every child before combining the results into a hash. [3](#0-2)  `get_bytecode_segment_lengths` supplies this structure from the CASM's `bytecode_segment_lengths` field, falling back to a single flat leaf only if the field is absent. [4](#0-3)  This hash is computed by `hash_inner`/`HashableCompiledClass::hash`, invoked whenever the sequencer needs to verify or (re)compute a declared class's `compiled_class_hash` (e.g., `DeclareTransaction::check_compile_class_hash_v2_declaration`, called on `casm.hash(&HashVersion::V2)`). [5](#0-4) [6](#0-5) 

The nesting depth is determined by the CASM's segmentation structure, which is derived by the Sierra→CASM compilation of an attacker-declared class (`SierraToCasmCompiler::compile`) and only bounded by `max_bytecode_size` (a felt-count limit, not a nesting-depth limit). [7](#0-6)  Because the segment structure follows the function/branch structure of the compiled program, a program crafted to compile into many deeply nested small segments (e.g., long chains of nested branches/functions each isolated into its own segment) can, in principle, produce a `NestedIntList` whose nesting depth approaches the size bound, causing `bytecode_hash_node`'s native call stack to grow linearly with that depth — with no explicit recursion-depth check protecting this path, unlike Cairo-level recursion, which is bounded by `RecursionDepthGuard`/step limits. [8](#0-7) 

Note: I was not able to fully confirm within the available context (a) the exact maximum achievable nesting depth the compiler's segmentation algorithm can produce for a given `max_bytecode_size`, or (b) whether any additional depth cap exists elsewhere in the declare pipeline (gateway validation, `apollo_sierra_compilation_config`) that would prevent this from reaching a stack-overflow-triggering depth in practice. This uncertainty is material to whether the recursion is genuinely unbounded enough to crash a sequencer process versus merely adding overhead.

### Impact Explanation
If the nesting depth can be pushed high enough, a single `declare` transaction (or any code path that recomputes `compiled_class_hash`, e.g. re-execution, class-hash migration in `blockifier/src/state/compiled_class_hash_migration.rs`) could cause a native stack overflow in the sequencer process. In Rust, stack overflow aborts the process (there is no catchable panic), unlike the Cairo VM's heap-simulated "stack," which merely returns an `Out of gas`/step-limit error as seen in the analogous `test_stack_overflow` test for Cairo Native execution. [9](#0-8)  A process crash triggered by processing a single submitted declare transaction (validation or execution) is a network-availability impact: it can repeatedly crash sequencer/full-node processes that attempt to validate or re-execute the same class, potentially halting block production or causing a network unable to confirm new transactions if triggered broadly.

### Likelihood Explanation
Likelihood depends entirely on whether an attacker can actually engineer a Sierra program that the trusted `cairo-lang-sierra-to-casm` compiler will segment into a deeply-nested `NestedIntList` (as opposed to a flat or shallowly-nested list). This requires influence over the compiler's internal segmentation heuristic, which is external, audited compiler logic not part of this repository (dependency-only aspect), and I could not verify from the available code how deep that nesting can realistically get for reasonable/maximal bytecode sizes. Because of this unresolved dependency-behavior question, likelihood cannot be confirmed as high with certainty from the code reviewed here.

### Recommendation
- Add an explicit recursion-depth (or explicit-stack-based iterative) implementation for `bytecode_hash_node`/`create_bytecode_segment_structure_inner`, mirroring the `RecursionDepthGuard` pattern already used to bound Cairo-level call recursion. [2](#0-1) 
- Validate/bound the maximum nesting depth of `bytecode_segment_lengths` as part of declare-transaction gateway validation, rejecting classes whose segment structure exceeds a safe depth, independent of `max_bytecode_size`.
- Convert the recursive traversal to an explicit-stack iterative algorithm to remove dependence on the native call stack entirely.

### Proof of Concept
Not independently reproducible from the indexed context: doing so requires crafting a Cairo1 source program that the pinned `cairo-lang-sierra-to-casm` version (referenced in `Cargo.lock`) compiles into a CASM with a deeply nested `bytecode_segment_lengths` `NestedIntList`, then feeding it through `SierraCompiler::compile` → `DeclareTransaction::check_compile_class_hash_v2_declaration` to trigger `bytecode_hash_node` at that depth. [10](#0-9)  Confirming actual exploitability (and thus final severity) requires validating the compiler's real segmentation depth behavior, which is outside what this indexed context could establish with certainty.

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

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L152-178)
```rust
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

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L197-204)
```rust
    /// Returns the lengths of the bytecode segments.
    /// If the length field is missing, the entire bytecode is considered a single segment.
    fn get_bytecode_segment_lengths(&self) -> Cow<'_, NestedIntList> {
        match &self.bytecode_segment_lengths {
            Some(bytecode_segment_lengths) => Cow::Borrowed(bytecode_segment_lengths),
            None => Cow::Owned(NestedIntList::Leaf(self.bytecode.len())),
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

**File:** crates/blockifier/src/blockifier/transaction_executor_test.rs (L538-579)
```rust
#[cfg(feature = "cairo_native")]
#[rstest::rstest]
/// Tests that Native can handle deep recursion calls without causing a stack overflow.
/// The recursive function must be complex enough to prevent the compiler from optimizing it into a
/// loop. This function was manually tested with increased maximum gas to ensure it reaches a stack
/// overflow.
///
/// Note: Testing the VM is unnecessary here as it simulates the stack where the stack in the heap
/// as a memory segment.
fn test_stack_overflow(#[values(true, false)] concurrency_enabled: bool) {
    let block_context = BlockContext::create_for_account_testing();
    let cairo_version = CairoVersion::Cairo1(RunnableCairo1::Native);
    let TestInitData { state, account_address, contract_address, mut nonce_manager } =
        create_test_init_data(&block_context.chain_info, cairo_version);
    let depth = felt!(1000000_u128);
    let entry_point_args = vec![depth];
    let calldata = create_calldata(contract_address, "test_stack_overflow", &entry_point_args);
    let invoke_tx = executable_invoke_tx(invoke_tx_args! {
        sender_address: account_address,
        calldata,
        nonce: nonce_manager.next(account_address),
        resource_bounds: ValidResourceBounds::create_for_testing_no_fee_enforcement(),
    });
    let account_tx = AccountTransaction::new_for_sequencing(invoke_tx);
    // Ensure the transaction is allocated the maximum gas limits.
    assert!(
        account_tx.resource_bounds().get_l2_bounds().max_amount
            >= block_context.versioned_constants.os_constants.execute_max_sierra_gas
                + block_context.versioned_constants.os_constants.validate_max_sierra_gas
    );
    // Run.
    let config = TransactionExecutorConfig::create_for_testing(concurrency_enabled);
    let mut executor = TransactionExecutor::new(state, block_context, config);
    let results = executor.execute_txs(&[account_tx.into()], None);

    let (tx_execution_info, _state_diff) = results[0].as_ref().unwrap();
    assert!(tx_execution_info.is_reverted());
    let err = tx_execution_info.revert_error.clone().unwrap().to_string();

    // Recursion is terminated by resource bounds before stack overflow occurs.
    assert!(err.contains("'Out of gas'"));
}
```

**File:** crates/apollo_compile_to_casm/src/lib.rs (L52-75)
```rust
impl SierraCompiler {
    pub fn new(compiler: SierraToCasmCompiler) -> Self {
        Self { compiler }
    }

    // TODO(Elin): move (de)serialization to infra. layer.
    #[instrument(skip(self, class), err)]
    #[sequencer_latency_histogram(COMPILATION_DURATION, true)]
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
}
```
