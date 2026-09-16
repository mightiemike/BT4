### Title
Uncontrolled recursion in compiled-class-hash bytecode segment hashing enables stack-overflow DoS on declare - (File: crates/starknet_api/src/contract_class/compiled_class_hash.rs)

### Summary
`bytecode_hash_node` in `crates/starknet_api/src/contract_class/compiled_class_hash.rs` recurses once per nesting level of the CASM `bytecode_segment_lengths` tree (`NestedIntList`) with no depth bound, exactly the CWE-674 pattern described in the xmldom report (pure recursive tree traversal, one native call frame per tree level, no guard). This function is invoked from `HashableCompiledClass::hash()`, which is called every time a compiled class hash is computed — both during Sierra→CASM compilation of a newly declared class (`apollo_compile_to_casm/src/lib.rs`) and during transaction execution/validation (`crates/blockifier/src/state/utils.rs::get_compiled_class_hash_v2`). [1](#0-0) 

### Finding Description
`bytecode_hash_node` walks the `NestedIntList` structure produced by the Sierra→CASM compiler for a declared contract class: [2](#0-1) 

Each `Node` variant recurses into every child via `iter_children().map(|child| bytecode_hash_node(...))`, and there is no maximum-depth check anywhere in this function or its caller `bytecode_hash`/`hash_inner`. The segmentation tree's shape (and therefore recursion depth) is determined by the compiled bytecode's function/branch structure, which in turn is derived from the Sierra program supplied by the class declarer — an attacker-influenced input. A Sierra program crafted with many levels of nested functions/branches can cause the compiler to emit a `bytecode_segment_lengths` tree with very deep nesting relative to its total size, since nesting depth is not itself constrained by `max_bytecode_size` (which only bounds total segment length, not tree depth).

This hashing routine is exercised on the class-declaration path:
- In `SierraCompiler::compile` (`apollo_compile_to_casm/src/lib.rs`), immediately after compiling a submitted Sierra class, `executable_class.hash(&HashVersion::V2)` is called in-process (outside of the resource-limited compiler subprocess) to compute the executable class hash returned to the gateway/mempool.
- In `crate::state::utils::get_compiled_class_hash_v2` and other `RunnableCompiledClass::hash()` call sites used by the blockifier when validating/verifying declared compiled-class hashes during execution. [3](#0-2) [4](#0-3) 

Unlike the actual Sierra→CASM compilation, which runs in a resource-limited subprocess (`ResourceLimits`, `max_cpu_time`, `max_memory_usage`) specifically to contain malicious/adversarial compiler inputs, the subsequent hash computation runs directly in the parent sequencer process with no stack-depth guard or iterative rewrite, so a stack overflow here crashes the sequencer/gateway process itself rather than an isolated subprocess. [5](#0-4) 

### Impact Explanation
A successful declare transaction (or its compilation/hash-verification path) containing a Sierra program that compiles to a CASM class with a deeply nested `bytecode_segment_lengths` tree can crash the process performing the hash computation via `RangeError`-equivalent stack overflow (Rust: SIGSEGV/abort on stack exhaustion). Since this hashing happens in the parent process (not the sandboxed compiler subprocess), it can take down the sequencer's class-compiler/gateway component directly — a network-availability impact reachable from a single, unprivileged, submitted declare transaction, matching the class-declarer threat model.

### Likelihood Explanation
Likelihood is uncertain without confirming precisely how deep the Cairo compiler's segment-tree nesting can grow relative to bounded bytecode size, and whether any implicit ceiling (e.g., function/branch count limits, or `max_bytecode_size`) effectively caps recursion depth. This uses the external cairo-lang compiler's output as input, so exploitability depends on being able to craft a Sierra program whose compiled bytecode segmentation tree is deep enough (order of thousands of levels, comparable to the xmldom crash thresholds) to exhaust the default thread stack — this needs empirical verification.

### Recommendation
Convert `bytecode_hash_node` (and `bytecode_hash`) to an iterative, explicit-stack traversal to remove the native-call-stack dependency on segment-tree depth, mirroring the fix pattern from the referenced xmldom advisory (`walkDOM`-style stack-based traversal). Additionally, enforce an explicit maximum nesting depth on `bytecode_segment_lengths` when deserializing/validating a compiled class, independent of total bytecode size, and reject compilation results that exceed it before hash computation.

### Proof of Concept
Not independently verified. A conceptual PoC would require: (1) constructing a Sierra program whose compiled CASM bytecode segmentation naturally nests to a large depth (e.g., via deeply chained function calls/branches) while staying within `max_bytecode_size`, (2) submitting it as a declare transaction so that `SierraCompiler::compile` → `executable_class.hash(&HashVersion::V2)` invokes `bytecode_hash_node` on the resulting deep `NestedIntList`, and (3) observing a stack overflow/crash in the compiling process. I was unable to confirm within the available tooling whether the cairo-lang compiler can actually produce sufficiently deep segment trees within existing bytecode-size limits — this needs to be validated in a live environment (e.g., via a Devin session with compiler access) before treating this as confirmed-exploitable rather than a plausible analog.

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

**File:** crates/blockifier/src/state/utils.rs (L13-26)
```rust
pub fn get_compiled_class_hash_v2(
    state_reader: &impl StateReader,
    class_hash: ClassHash,
    _compiled_class: &RunnableCompiledClass,
) -> StateResult<CompiledClassHash> {
    match state_reader.get_compiled_class(class_hash) {
        Ok(RunnableCompiledClass::V0(_)) | Err(StateError::UndeclaredClassHash(_)) => {
            Err(StateError::MissingCompiledClassHashV2(class_hash))
        }
        Ok(RunnableCompiledClass::V1(class)) => Ok(class.hash(&HashVersion::V2)),
        #[cfg(feature = "cairo_native")]
        Ok(RunnableCompiledClass::V1Native(class)) => Ok(class.hash(&HashVersion::V2)),
        Err(e) => Err(e),
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
