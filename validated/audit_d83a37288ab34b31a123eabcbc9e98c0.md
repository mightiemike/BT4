### Title
Unbounded recursion in compiled-class-hash bytecode segment hashing enables stack-overflow DoS via declare transaction - ([File: crates/starknet_api/src/contract_class/compiled_class_hash.rs])

### Summary
The compiled class hash computation recursively walks the `NestedIntList` bytecode segment structure with no depth bound. A contract declarer can submit a Sierra program crafted so that the Sierra→CASM compiler emits a deeply nested `bytecode_segment_lengths` tree, causing unbounded recursion when the sequencer computes the compiled class hash, crashing the process with a stack overflow.

### Finding Description
`bytecode_hash_node` in `crates/starknet_api/src/contract_class/compiled_class_hash.rs:111-132` recurses into every child of a `NestedIntList::Node` with **no depth tracking or limit whatsoever**: [1](#0-0) 

This mirrors the reported bug class exactly: a validator/hasher that recurses over nested structures without any depth guard (unlike, e.g., the MongoDB BSON validator that at least attempted depth tracking but reset it on re-entry — here there is no tracking at all).

This function is reached via `HashableCompiledClass::hash`, invoked from the Sierra compiler pipeline right after a declare transaction's class is compiled: [2](#0-1) 

The call site is `SierraCompiler::compile`, executed for every declared class before it is admitted: [3](#0-2) 

which is invoked from `ClassManager::add_class`, the class-declaration entry point reachable from an unprivileged declare transaction: [4](#0-3) 

The `bytecode_segment_lengths` (`NestedIntList`) tree is produced by the external Sierra-to-CASM compiler based on the *structure* of the submitted Sierra program (e.g., deeply nested functions/branches create deeply nested segments). I could not find any explicit limit in the codebase on the **nesting depth** of `bytecode_segment_lengths` — only `max_bytecode_size` (total bytecode length) is enforced in `SierraToCasmCompiler::compile`/`SierraCompilationConfig`: [5](#0-4) 

A bytecode-size limit does not bound nesting depth: an attacker can construct a program whose segment tree is very deep while total bytecode length stays under the size cap (e.g., long chains of singleton nested segments), so the size check does not prevent deep recursion in `bytecode_hash_node`.

Note: a related recursive helper, `NestedFeltCounts::new_inner` in `crates/blockifier/src/execution/contract_class.rs:163-194`, does assert `segmentation_depth <= 1`, so it fails fast rather than recursing deeply — but `bytecode_hash_node` has no equivalent guard. Similarly, `create_bytecode_segment_structure_inner` in `crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs:277-307` is also unboundedly recursive over the same `NestedIntList` and is used during Starknet OS re-execution, extending the same risk into the proving/re-execution path.

### Impact Explanation
If the recursion depth exceeds the thread stack size, the process crashes with a stack overflow (Rust aborts on stack overflow; it cannot be caught with `catch_unwind`). Since `add_class`/`SierraCompiler::compile` runs synchronously as part of declare-transaction processing (class manager / gateway compilation pipeline) on every full node/sequencer that processes the declare transaction or later re-executes/re-derives the compiled class hash (including via Starknet OS re-execution), a single malicious declare transaction can crash sequencer or full-node processes, causing a denial of service and potentially halting the network's ability to process further transactions until the crash is diagnosed and mitigated.

### Likelihood Explanation
This is reachable by any unprivileged account submitting a declare (`DeclareTransaction`) with a specially crafted Sierra program, i.e., a single submitted transaction from any external declarer — no privileged role, mocked path, or op access required. It is a Medium-to-High-likelihood analog because it only requires crafting a Sierra program whose compiled structure yields deep segment nesting, which is plausible using recursive/deeply-nested control flow patterns compiled by the standard Cairo compiler, though the exact required nesting depth and stack-size threshold in the sequencer's deployment configuration were not fully verified from the code alone (I could not confirm the exact thread stack size used by the class-manager/gateway/compiler process, nor whether any downstream depth cap exists in the external Sierra-to-CASM compiler binary itself that would bound `bytecode_segment_lengths` nesting before it reaches `bytecode_hash_node`).

### Recommendation
Add explicit depth tracking/limits to `bytecode_hash_node` (and the analogous `create_bytecode_segment_structure_inner` in `starknet_os`), rejecting or erroring out on excessively deep `NestedIntList` structures before recursion proceeds, mirroring the existing `RecursionDepthGuard` pattern already used for other recursive execution paths in `crates/blockifier/src/execution/entry_point.rs:706-733`. Additionally, enforce a maximum nesting-depth bound on declared classes' bytecode segment structure during gateway/class-manager validation, independent of the flat `max_bytecode_size` check.

### Proof of Concept
Conceptual PoC (not executed, derived from code reading):
1. Construct a Cairo 1 source/Sierra program engineered to produce a `CasmContractClass` whose `bytecode_segment_lengths` is a deeply right-nested `NestedIntList::Node` chain (e.g., via deeply nested match/if-else branches, which the Sierra-to-CASM compiler's bytecode segmentation logic tends to represent as nested segments) — depth on the order of the available stack frames divided by the frame size of `bytecode_hash_node`.
2. Submit this class via a standard `DeclareTransaction` to the gateway.
3. `ClassManager::add_class` → `SierraCompiler::compile` → `executable_class.hash(&HashVersion::V2)` → `hash_inner` → `bytecode_hash` → `bytecode_hash_node` recurses once per nesting level with no depth check, exhausting the stack and crashing the process handling the declare transaction (and, later, any node/prover re-deriving or re-verifying the compiled class hash, e.g., during Starknet OS re-execution).

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

**File:** crates/apollo_class_manager/src/class_manager.rs (L70-113)
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
        debug!(
            %class_hash,
            compiled_class_hash = %executable_class_hash_v2,
            compilation_elapsed_ms = compilation_start_time.elapsed().as_millis(),
            class_size_bytes =
                class.size().map_or("Failed to get class size".to_owned(), |size| size.to_string()),
            casm_size_bytes =
                raw_executable_class.size().map_or("Failed to get casm size".to_owned(), |size| size.to_string()),
            "Finished compiling class."
        );

        self.validate_class_length(&raw_executable_class)?;
        Self::validate_class_version(&sierra_class)?;
        self.classes.set_class(
            class_hash,
            class,
            executable_class_hash_v2,
            raw_executable_class,
        )?;

        let class_hashes = ClassHashes { class_hash, executable_class_hash_v2 };
        Ok(class_hashes)
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
