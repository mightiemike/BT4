## Finding

### Title
Stack overflow via unbounded recursion in `bytecode_hash_node` when computing compiled class hash for declared classes - (File: `crates/starknet_api/src/contract_class/compiled_class_hash.rs`)

### Summary
`bytecode_hash_node`, the function used to compute a Sierra-compiled contract's `compiled_class_hash`, recurses once per nesting level of the `NestedIntList` bytecode-segment structure with no depth bound. This function is invoked by `SierraCompiler::compile` in the sequencer's class-compilation component, in the *same process* and *outside* the resource-limited (CPU/memory-bounded) subprocess used for the actual Sierra→CASM compilation step. A contract declarer fully controls the Cairo source (and thus the resulting control-flow/segment nesting) of the class being declared, allowing them to drive this unguarded recursion to a stack overflow and crash the class-compiler / sequencer process handling their `DECLARE` transaction.

### Finding Description
The compiled-class-hash algorithm partitions the CASM bytecode into a tree of segments (`NestedIntList`) and recursively hashes it: [1](#0-0) 

`bytecode_hash_node` recurses into `node.iter_children()` for every internal `Node`, with no limit on nesting depth — contrast this with `NestedFeltCounts::new_inner` in `blockifier/src/execution/contract_class.rs`, which explicitly asserts `segmentation_depth <= 1` for the same kind of `NestedIntList` structure, showing the project is aware such structures need depth guarding but omitted the guard here: [2](#0-1) 

The nesting depth of `bytecode_segment_lengths` is determined by the compiler based on the contract's control-flow structure (nested branches/functions), which is fully attacker-controlled via the declared Sierra program, bounded only by `max_bytecode_size` (81920 felts by default): [3](#0-2) 

Critically, `hash()` (which calls `bytecode_hash_node` transitively) is called in `SierraCompiler::compile` *after* the sandboxed subprocess compilation returns, in the unsandboxed parent process: [4](#0-3) 

The actual Sierra→CASM compilation runs in a child process with CPU/memory `rlimit`s applied (no stack-size limit is set), via `compile_with_args`/`ResourceLimits`: [5](#0-4) [6](#0-5) 

But the subsequent `executable_class.hash(&HashVersion::V2)` call (containing the unbounded recursion) executes back in the parent `SierraCompiler`/class-manager process — the very process that must remain available to keep accepting and processing `DECLARE` transactions for the sequencer. A stack overflow there is a hard process abort (SIGSEGV / illegal instruction), not a recoverable `Result::Err`.

### Impact Explanation
A crash of the sequencer's class-compilation/class-manager component halts processing of `DECLARE` transactions (and any component sharing that process/runtime), which is a network-availability impact: the node becomes unable to confirm new (or at least new class-declaring) transactions until restarted. Because this reproduces the "unprivileged transaction path causes an out-of-bounds stack overflow crash" pattern from CVE-2023-41268, and is reachable by any account able to submit a `DECLARE` transaction, this satisfies the required "network unable to confirm new transactions" bar for Medium/High severity.

### Likelihood Explanation
Likelihood is moderate-to-high: any account with gas to pay for a `DECLARE` transaction can submit a contract whose compiled bytecode's segment tree is deeply/linearly nested (e.g., via deeply nested `if/else`/`match` chains or many small nested functions) up to the `max_bytecode_size` limit (81920), which is large enough to plausibly exceed default thread/process stack sizes when unwound recursively frame-by-frame in `bytecode_hash_node`. No additional privileges, timing, or races are required — a single crafted class submission is sufficient to trigger the code path.

### Recommendation
Convert `bytecode_hash_node` (and `bytecode_hash`) in `crates/starknet_api/src/contract_class/compiled_class_hash.rs` to an iterative/worklist algorithm instead of unbounded native recursion, or enforce an explicit maximum nesting-depth check (mirroring the `segmentation_depth <= 1` guard already used in `blockifier/src/execution/contract_class.rs`) before recursing, rejecting classes whose segment structure exceeds the bound. Additionally, consider moving the hash computation into the same resource/stack-isolated subprocess as the Sierra→CASM compilation step, so any failure there does not crash the long-lived sequencer component process.

### Proof of Concept
1. Craft a Cairo1/Sierra contract whose control flow compiles into a CASM bytecode segment tree with very deep nesting (e.g., thousands of sequentially nested `if`/`match` branches or nested nested functions), sized to approach `max_bytecode_size` (81920 felts, per `sierra_compiler_config.max_bytecode_size`).
2. Submit this contract via a `DECLARE` transaction to the sequencer's gateway.
3. The gateway forwards the class to the class-manager/`SierraCompiler` component; the class successfully compiles Sierra→CASM in the sandboxed subprocess (bytecode length is within `max_bytecode_size`).
4. `SierraCompiler::compile` (`crates/apollo_compile_to_casm/src/lib.rs:69`) then calls `executable_class.hash(&HashVersion::V2)` in the parent process, which recurses through `bytecode_hash_node` once per nesting level of the segment tree, exhausting the thread/process stack and crashing the component that handles `DECLARE` transactions.

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

**File:** crates/apollo_node/resources/config_schema.json (L3737-3741)
```json
  "sierra_compiler_config.max_bytecode_size": {
    "description": "Limitation of compiled CASM bytecode size (felts).",
    "privacy": "Public",
    "value": 81920
  },
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

**File:** crates/apollo_compilation_utils/src/compiler_utils.rs (L17-50)
```rust
pub fn compile_with_args(
    compiler_binary_path: &Path,
    contract_class: ContractClass,
    additional_args: &[&str],
    resource_limits: ResourceLimits,
) -> Result<Vec<u8>, CompilationUtilError> {
    // Create a temporary file to store the Sierra contract class.
    let serialized_contract_class = serde_json::to_string(&contract_class)?;

    let mut temp_file = NamedTempFile::new()?;
    temp_file.write_all(serialized_contract_class.as_bytes())?;
    let temp_file_path = temp_file.path().to_str().ok_or(CompilationUtilError::UnexpectedError(
        "Failed to get temporary file path".to_owned(),
    ))?;

    // Set the parameters for the compile process.
    let mut command = Command::new(compiler_binary_path.as_os_str());
    command.arg(temp_file_path).args(additional_args);

    // Apply the resource limits to the command.
    resource_limits.apply(&mut command);

    // Run the compile process.
    let compile_output = command.output()?;

    if !compile_output.status.success() {
        let stderr_output = String::from_utf8(compile_output.stderr)
            .unwrap_or_else(|_| "Failed to decode stderr output".to_string());

        let error_message = format_compiler_error(&stderr_output, &compile_output.status);
        return Err(CompilationUtilError::CompilationError(error_message));
    }
    Ok(compile_output.stdout)
}
```

**File:** crates/apollo_compilation_utils/src/resource_limits/resource_limits_unix.rs (L41-76)
```rust
pub struct ResourceLimits {
    /// A limit (in seconds) on the amount of CPU time that the process can consume.
    cpu_time: Option<RLimit>,
    /// The maximum size (in bytes) of files that the process may create.
    file_size: Option<RLimit>,
    /// The maximum size (in bytes) of the process’s virtual memory (address space).
    memory_size: Option<RLimit>,
}

impl ResourceLimits {
    pub fn new(
        cpu_time: Option<u64>,
        file_size: Option<u64>,
        memory_size: Option<u64>,
    ) -> ResourceLimits {
        ResourceLimits {
            cpu_time: cpu_time.map(|t| RLimit {
                resource: Resource::CPU,
                soft_limit: t,
                hard_limit: t,
                units: "seconds".to_string(),
            }),
            file_size: file_size.map(|x| RLimit {
                resource: Resource::FSIZE,
                soft_limit: x,
                hard_limit: x,
                units: "bytes".to_string(),
            }),
            memory_size: memory_size.map(|y| RLimit {
                resource: Resource::AS,
                soft_limit: y,
                hard_limit: y,
                units: "bytes".to_string(),
            }),
        }
    }
```
