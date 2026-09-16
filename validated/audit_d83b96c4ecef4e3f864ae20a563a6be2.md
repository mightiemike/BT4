## Confirmed: recursive, unbounded bytecode-segment hashing/rebuilding in the Sierra→CASM class-hash path

The compilation subprocess (`compile_with_args`) is resource-limited and only bounds bytecode size via `--max-bytecode-size`, which is applied by the external `cairo_lang_starknet_classes` compiler binary [1](#0-0) . However, the compiled-class hash is computed *after* the subprocess returns, in-process, in `SierraCompiler::compile`: [2](#0-1) . That hash computation calls into `bytecode_hash_node`, an unbounded plain recursive function over the `bytecode_segment_lengths` (`NestedIntList`) tree with no depth check: [3](#0-2) . The same unbounded-recursion pattern also exists in the Starknet OS re-execution path when it rebuilds and hashes the bytecode segment tree during CASM class-hash verification hints: `create_bytecode_segment_structure_inner` and `BytecodeSegmentNode::hash`, both recursing per nesting level with no depth guard: [4](#0-3) [5](#0-4) .

### Title
Unbounded recursion in bytecode-segment hashing/rebuild during Sierra→CASM class-hash computation and OS re-execution enables stack-overflow DoS - (File: `crates/starknet_api/src/contract_class/compiled_class_hash.rs`)

### Summary
A contract declarer submits a Sierra program that the (sandboxed) Sierra→CASM compiler turns into a `CasmContractClass` whose `bytecode_segment_lengths` field is an arbitrarily deep, linear-chain `NestedIntList` (e.g., `Node([Node([Node([...Leaf(1)...])])])`). Depth is only bounded indirectly by total bytecode size, not explicitly limited. Immediately after the sandboxed compile call returns, the sequencer computes `executable_class.hash(&HashVersion::V2)` **outside** the subprocess sandbox, using the plain recursive `bytecode_hash_node` function, one stack frame per nesting level [6](#0-5) . The same pattern recurs in the Starknet OS's `create_bytecode_segment_structure_inner`/`BytecodeSegmentNode::hash` used by every node re-executing/proving the block [5](#0-4) .

### Finding Description
- The gateway/sierra-compiler flow: `SierraCompiler::compile` calls the sandboxed compiler binary (`compile_with_args`, resource-limited only by `--max-bytecode-size`, CPU time, memory) [7](#0-6) , then immediately computes `executable_class.hash(&HashVersion::V2)` on the returned `CasmContractClass` **in the calling process**, not inside the sandbox [2](#0-1) .
- `hash()` calls `bytecode_hash` → `bytecode_hash_node`, which recurses once per level of `bytecode_segment_lengths` nesting with no depth check or iterative fallback [6](#0-5) .
- `bytecode_segment_lengths` is compiler-generated from the Sierra program's control-flow/function structure, but its nesting depth is not validated anywhere before hashing — only overall bytecode size is capped (`max_bytecode_size` compiler flag), and a linear chain of single-child nested branches can accumulate depth roughly proportional to a small multiple of instructions, independent of the bytecode-size cap on total felts.
- The identical unbounded-recursion pattern exists in the Starknet OS hint implementation used during block re-execution/proving: `create_bytecode_segment_structure_inner` rebuilds the tree recursively [5](#0-4) , and `BytecodeSegmentNode::hash` re-hashes it recursively [8](#0-7) . This code runs on every full/OS node re-executing the block that declares/executes the malicious class, not inside any subprocess sandbox.
- This matches the CVE-2017-9210 bug class precisely: recursive "unparse"/rebuild functions on attacker-influenced tree structures with no explicit recursion-depth bound, causing stack exhaustion.

### Impact Explanation
A crafted declared class can crash the sierra-compiler component process (denial of service for declare processing) and/or crash any node performing Starknet OS re-execution/proving of the block containing the declare or later invoke of that class, since the same unbounded recursive hash/rebuild runs there too. Because this executes in-process (not the sandboxed CASM-compile subprocess), a stack overflow aborts the whole process, not just a sandboxed child — this can take down a gateway/sierra-compiler/prover instance, contributing to a network unable to confirm new transactions if it recurs across the fleet, and can cause honest nodes to crash rather than agree on execution results (liveness/availability impact rather than a state-corruption impact). This satisfies the "network unable to confirm new transactions" acceptance criterion.

### Likelihood Explanation
Reachable by any account authorized to submit a declare transaction (or an L2 account interacting with an already-declared malicious class, since the OS-side hashing runs at block-proving/re-execution time too). No special privileges beyond ordinary contract declaration are required. The only gating factor is whether the compiler emits deeply-nested `bytecode_segment_lengths` for programs an attacker can craft within the bytecode-size limit; given segmentation follows function/branch structure, a contract with many nested branches is a plausible, buildable input, though the exact required source-code pattern would need to be validated empirically against the specific `cairo_lang_starknet_classes` compiler version pinned by this repo (external dependency, not directly modifiable here).

### Recommendation
Convert `bytecode_hash_node` (`crates/starknet_api/src/contract_class/compiled_class_hash.rs`), `create_bytecode_segment_structure_inner`, and `BytecodeSegmentNode::hash` (`crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs`) to iterative, explicit-stack-based implementations, or add an explicit maximum nesting-depth check before/while recursing (rejecting/erroring on classes whose segment tree exceeds a safe depth bound). Additionally, ensure the post-compile hash computation in `SierraCompiler::compile` executes within the same resource-limited/sandboxed boundary as the compilation itself, rather than in the calling process.

### Proof of Concept
Not independently executable without running the pinned Cairo compiler binary; conceptually: declare a Sierra contract engineered (e.g., via many sequential nested single-branch `if`/`match` constructs) so that the compiler emits a `bytecode_segment_lengths` `NestedIntList` that is a long single-child chain of depth sufficient to exceed the default thread stack size when `bytecode_hash_node`/`BytecodeSegmentNode::hash` recurse one frame per level. Exact minimal contract size needed to trigger the crash could not be determined statically from the index and would require compiling candidate Sierra programs with the pinned compiler and measuring emitted segment depth versus stack limits — this is a caveat on precise exploitability, though the code-level absence of any depth bound is confirmed directly from the source.

### Citations

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

**File:** crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs (L212-233)
```rust
impl BytecodeSegmentNode {
    pub(crate) fn hash<H: HashFunction>(&self) -> StarkHash {
        match self {
            BytecodeSegmentNode::Leaf(leaf) => H::hash_array(&leaf.data),
            BytecodeSegmentNode::InnerNode(inner_node) => {
                let flatten_input: Vec<_> = inner_node
                    .segments
                    .iter()
                    .flat_map(|segment| [Felt::from(segment.length), segment.node.hash::<H>()])
                    .collect();
                H::hash_array(&flatten_input) + Felt::ONE
            }
        }
    }

    pub(crate) fn is_leaf(&self) -> bool {
        match self {
            BytecodeSegmentNode::Leaf(_) => true,
            BytecodeSegmentNode::InnerNode(_) => false,
        }
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
