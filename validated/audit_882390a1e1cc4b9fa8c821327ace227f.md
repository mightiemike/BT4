## Finding: Unbounded native-stack recursion when hashing a compiled class's bytecode segment tree can crash sequencer/gateway processes on a crafted `declare`

### Title
Unbounded Recursion in `bytecode_hash_node` During Compiled Class Hash Computation Causes Stack-Overflow DoS — (File: `crates/starknet_api/src/contract_class/compiled_class_hash.rs`)

### Summary
`CasmContractClass::hash()` recursively walks the `bytecode_segment_lengths` tree (`NestedIntList`) to compute the compiled-class hash via `bytecode_hash_node`. This recursion has no depth bound and is not step/gas-metered like Cairo VM execution — it is plain native Rust recursion executed directly on the calling process's stack, unlike PoDoFo's `PdfParser::ReadDocumentStructure`, which likewise recurses without a depth cap over an attacker-influenced document tree.

### Finding Description
`bytecode_hash_node` recurses once per level of the `NestedIntList` tree that describes how the CASM bytecode is segmented: [1](#0-0) 

The tree it walks (`bytecode_segment_lengths`) is taken directly from the `CasmContractClass` produced by compiling a user-submitted Sierra program: [2](#0-1) 

This is invoked by the gateway/Sierra compiler component right after compiling a submitted `declare` transaction's Sierra program, on the gateway's own thread with no dedicated large/guarded stack: [3](#0-2) 

The Sierra-to-CASM compilation itself runs isolated in a resource-limited subprocess (`compile_with_args` with `ResourceLimits`), but the resulting `CasmContractClass` (including `bytecode_segment_lengths`) is deserialized back into the parent gateway process, and `hash()`/`bytecode_hash_node` then runs unshielded: [4](#0-3) 

The segment tree's nesting depth is derived from the function/branch structure the *compiler* emits for the Sierra program, not from raw byte length; only `max_bytecode_size` (total felt count) constrains it — pathological Sierra control flow (e.g. a long chain of small nested match/branch constructs) can drive segmentation to a nesting depth proportional to the number of branches, independent of any explicit recursion-depth cap. This is unlike Cairo-VM execution recursion, which the codebase explicitly protects against stack exhaustion via `RUST_MIN_STACK`/dedicated large-stack threads for VM/Native execution: [5](#0-4) [6](#0-5) 

No equivalent stack-size guard or explicit iteration-based rewrite exists for `bytecode_hash_node` / `bytecode_hash_internal_node` (native Rust recursion), nor for its sibling `get_visited_segments`/`NestedFeltCounts::new` traversal used elsewhere in the blockifier when handling `bytecode_segment_felt_sizes`: [7](#0-6) 

Crucially, this same hash computation is not confined to the gateway: every sequencer/full node that must independently verify a declared class's compiled-class hash (state commitment, block validation, OS re-execution, class-hash migration estimation) performs the identical unbounded recursive walk over the attacker-shaped segment tree.

### Impact Explanation
A crafted Sierra program compiled to CASM with an artificially deep segment-length tree causes `bytecode_hash_node` to recurse deeply enough to exhaust the native stack of the process computing the hash. In Rust, stack overflow triggers process abort rather than a catchable error — this crashes the gateway process handling `declare` validation, and because every honest node must recompute the same compiled-class hash (for stateful validation, block building, and state commitment), the same malicious class can repeatedly crash any sequencer or full node that processes it, leading to a network unable to confirm new transactions (liveness DoS) rather than mere resource exhaustion of a single node.

### Likelihood Explanation
The trigger is a single `declare` transaction with an adversarially structured (but otherwise valid) Sierra program designed to make the trusted compiler emit a deeply nested `bytecode_segment_lengths` tree (e.g., via a long chain of small nested branches/functions within `max_bytecode_size`). No special privileges beyond submitting an ordinary `declare` are required, making this reachable by any unprivileged transaction sender.

### Recommendation
Convert `bytecode_hash_node` (and the analogous `bytecode_hash_internal_node`/`get_visited_segments`/`NestedFeltCounts::new` traversals) to an explicit iterative (stack-based) algorithm instead of native recursion, or impose and enforce a strict maximum segment-tree depth at contract-class validation time (rejecting classes whose derived segmentation exceeds the bound) before the hash is ever computed on any node.

### Proof of Concept
1. Author a Cairo1 contract whose control flow (deeply nested `if`/`match` branches or many small nested functions) causes `cairo-lang-sierra-to-casm` to emit `bytecode_segment_lengths` as a `NestedIntList` with recursion depth in the tens of thousands, while staying within `max_bytecode_size`.
2. Submit this contract via a `declare` transaction to the gateway.
3. After Sierra→CASM compilation succeeds in the isolated subprocess, the gateway calls `executable_class.hash(&HashVersion::V2)` (`crates/apollo_compile_to_casm/src/lib.rs:69`), which recurses into `bytecode_hash_node`/`bytecode_hash_internal_node` on the gateway's own thread, exhausting the stack and aborting the process — and every other sequencer/full node that later verifies or re-executes the same declared class performs the identical unbounded recursive hash computation.

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

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L330-349)
```rust
            std::thread::scope(|s| {
                std::thread::Builder::new()
                    // when running Cairo natively, the real stack is used and could get overflowed
                    // (unlike the VM where the stack is simulated in the heap as a memory segment).
                    //
                    // We pre-allocate the stack here, and not during Native execution (not trivial), so it
                    // needs to be big enough ahead.
                    // However, making it very big is wasteful (especially with multi-threading).
                    // So, the stack size should support calls with a reasonable gas limit, for extremely deep
                    // recursions to reach out-of-gas before hitting the bottom of the recursion.
                    //
                    // The gas upper bound is MAX_POSSIBLE_SIERRA_GAS, and sequencers must not raise it without
                    // adjusting the stack size.
                    .stack_size(self.config.stack_size)
                    .spawn_scoped(s, || self.execute_txs_sequentially_inner(&txs, execution_deadline))
                    .expect("Failed to spawn thread")
                    .join()
                    .expect("Failed to join thread.")
            })
        }
```

**File:** crates/native_blockifier/.cargo/config.toml (L1-6)
```text
[env]
# Enforce native_blockifier linking with pypy3.9.
PYO3_PYTHON = "/usr/local/bin/pypy3.9"
# Increase Rust stack size.
# This should be large enough for `MAX_ENTRY_POINT_RECURSION_DEPTH` recursive entry point calls.
RUST_MIN_STACK = "4194304" #  4 MiB
```

**File:** crates/blockifier/src/execution/contract_class.rs (L484-492)
```rust
    // Returns the set of segments that were visited according to the given visited PCs.
    // Each visited segment must have its starting PC visited, and is represented by it.
    fn get_visited_segments(
        &self,
        visited_pcs: &HashSet<usize>,
    ) -> Result<Vec<usize>, TransactionExecutionError> {
        let mut reversed_visited_pcs: Vec<_> = visited_pcs.iter().cloned().sorted().rev().collect();
        get_visited_segments(&self.bytecode_segment_felt_sizes, &mut reversed_visited_pcs, &mut 0)
    }
```
