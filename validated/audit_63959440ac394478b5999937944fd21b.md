### Title
Denial of Service via unbounded-depth `bytecode_segment_lengths` on Declare — panic/stack-overflow crash - (File: crates/blockifier/src/execution/contract_class.rs)

### Summary
When a Cairo 1 class is declared, the sequencer builds a `CompiledClassV1` from the compiled CASM. This conversion calls `NestedFeltCounts::new`, which recursively walks the attacker-influenced `bytecode_segment_lengths` (`NestedIntList`) tree and hard-`assert!`s that the recursion depth never exceeds 1 [1](#0-0) . This mirrors the SurrealDB bug class: an untrusted, recursively-defined structure is walked with one recursive call per nesting level and no depth budget is enforced before recursing — here the "budget" is an `assert!` that panics (hard process abort) instead of returning a validation error, and in the OS's parallel implementation there is no bound at all.

### Finding Description
`CasmContractClass::bytecode_segment_lengths` is a `NestedIntList` (`Leaf`/`Node`) that is embedded in the compiled CASM produced for a declared class and is serialized/deserialized as ordinary untrusted data along the declare pipeline [2](#0-1) . Every time this CASM is converted into an executable `CompiledClassV1` (on declare, and again whenever the class is loaded from state for execution), the code computes:

```
let bytecode_segment_felt_sizes = NestedFeltCounts::new(&class.get_bytecode_segment_lengths(), &class.bytecode);
``` [3](#0-2) 

`NestedFeltCounts::new_inner` recurses once per `Node` level and explicitly asserts the depth never exceeds 1:
```
fn new_inner(..., segmentation_depth: usize) -> (Self, usize) {
    assert!(segmentation_depth <= 1, "Only supported for segmentation depth at most 1.");
    ...
}
``` [4](#0-3) 

This is a Rust `assert!`, not a `Result`-returning check — it panics the current thread rather than gracefully rejecting the malformed class. This conversion runs in-process (not inside the resource-isolated Sierra→CASM compiler subprocess) both during gateway stateful validation (`try_declare` → `tx.contract_class().try_into()?` → `CompiledClassV1::try_from`) [5](#0-4)  and during batcher/executor re-execution/state reads, so a panic here can take down the validating/executing sequencer process (or, if isolated in a worker thread pool, trigger the abort-on-panic guard used for concurrency workers) [6](#0-5) .

Separately, the Starknet OS's equivalent structure builder has **no depth bound at all**:
```
pub(crate) fn create_bytecode_segment_structure_inner(
    bytecode: &[Felt],
    bytecode_segment_lengths: NestedIntList,
    bytecode_offset: usize,
) -> (BytecodeSegmentNode, usize) {
    match bytecode_segment_lengths {
        NestedIntList::Leaf(length) => { ... }
        NestedIntList::Node(lengths) => {
            for item in lengths {
                let (current_structure, item_len) =
                    create_bytecode_segment_structure_inner(bytecode, item, bytecode_offset);
                ...
            }
            ...
        }
    }
}
``` [7](#0-6) 
This function is invoked for every declared Cairo-1 class during OS hint processing (`LoadClassesAndBuildBytecodeSegmentStructures`) that runs as part of Starknet OS re-execution/proving [8](#0-7) . An arbitrarily-deep `NestedIntList::Node(Node(Node(...)))` chain here recurses one call per level with no limit, which is exactly the SurrealDB bug pattern: a flat/nested untrusted structure walked recursively without a depth budget, eventually overflowing the thread stack.

Both `bytecode_hash`/`bytecode_hash_node` in the compiled-class-hash computation also recurse per nesting level without an explicit depth cap, relying only on generic structural correctness, not an enforced maximum [9](#0-8) .

I was not able to fully confirm within the available searches whether the gateway's trusted Sierra→CASM compiler subprocess (`apollo_compile_to_casm`) can itself be coerced by a crafted-but-otherwise-valid Sierra program into emitting a `bytecode_segment_lengths` with nesting depth greater than 1 (the compiler determines segmentation based on the contract's own function/control-flow structure). This is the key unresolved question for exploitability of the blockifier-side `assert!` panic path, since normally the compiler is the only producer of this field for freshly-declared classes: [10](#0-9) . If the trusted compiler can be driven to emit depth > 1 (e.g., via nested nested-function segmentation for large/complex contracts), the `assert!` panic is directly triggerable by an unprivileged declarer.

### Impact Explanation
If the `assert!(segmentation_depth <= 1)` in `NestedFeltCounts::new_inner` can be triggered by a legitimately-compiled class (or if the OS's unbounded `create_bytecode_segment_structure_inner` is fed a deep-enough tree to overflow the stack), this is a process-abort/crash reachable from a single Declare transaction — a Medium/High DoS denying the whole node (or a subset of block-building/proving nodes) until restart, matching the CVSS profile of the referenced advisory (availability-only, no data corruption).

### Likelihood Explanation
Reachability requires either (a) the trusted Sierra compiler to produce a `bytecode_segment_lengths` of depth > 1 for some valid Sierra program (unconfirmed from available code/context), or (b) any code path that deserializes a `CasmContractClass`/`NestedIntList` directly from network/storage data without re-validating structural depth before it reaches `NestedFeltCounts::new` or `create_bytecode_segment_structure_inner`. Because the depth restriction and the OS's unbounded recursion are both un-normalized against a maximum-recursion config (unlike the analogous `max_recursion_depth`/`RecursionDepthGuard` used elsewhere in the blockifier for Cairo call-stack recursion, cf. [11](#0-10) ), likelihood cannot be fully assessed without confirming whether the compiler subprocess output can actually exceed depth 1, or whether the `NestedIntList` field can be otherwise attacker-supplied directly (e.g. via legacy V2 declare flows that historically may not always re-derive the CASM from Sierra locally). This should be verified with a live/dynamic test (crafting a Sierra program to exercise deep segmentation, or fuzzing `NestedIntList` deserialization).

### Recommendation
- Replace the `assert!(segmentation_depth <= 1, ...)` in `NestedFeltCounts::new_inner` with a proper `Result`-returning validation error so malformed/deep `bytecode_segment_lengths` are rejected gracefully instead of panicking the process [4](#0-3) .
- Add an explicit, configurable maximum recursion/nesting-depth check (analogous to `max_recursion_depth`/`RecursionDepthGuard`) to `create_bytecode_segment_structure_inner` in the Starknet OS before recursing [7](#0-6) , and to `bytecode_hash_node` in `crates/starknet_api/src/contract_class/compiled_class_hash.rs`.
- Validate `bytecode_segment_lengths` depth/shape as part of stateless/stateful declare validation, before it is trusted anywhere in the execution or OS pipeline, regardless of whether it originates from the trusted compiler or from stored/synced data.

### Proof of Concept
Not independently reproduced in this analysis; conceptual PoC: craft a `CasmContractClass` (or a Sierra program that compiles to one) whose `bytecode_segment_lengths` field is `NestedIntList::Node(vec![NestedIntList::Node(vec![NestedIntList::Node(...)])])` nested deeply enough to either (a) trigger the `assert!(segmentation_depth <= 1)` panic in `NestedFeltCounts::new_inner` on the first Declare/state-load, or (b) exceed the thread stack via unbounded recursion in `create_bytecode_segment_structure_inner` during Starknet OS re-execution of that class's declare transaction.

### Citations

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

**File:** crates/blockifier/src/execution/contract_class.rs (L667-668)
```rust
        let bytecode_segment_felt_sizes =
            NestedFeltCounts::new(&class.get_bytecode_segment_lengths(), &class.bytecode);
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

**File:** crates/blockifier/src/transaction/transactions.rs (L385-400)
```rust
/// Attempts to declare a contract class by setting the contract class in the state with the
/// specified class hash.
fn try_declare<S: State>(
    tx: &DeclareTransaction,
    state: &mut S,
    class_hash: ClassHash,
    compiled_class_hash: Option<CompiledClassHash>,
) -> TransactionExecutionResult<()> {
    match state.get_compiled_class(class_hash) {
        Err(StateError::UndeclaredClassHash(_)) => {
            // Class is undeclared; declare it.
            state.set_contract_class(class_hash, tx.contract_class().try_into()?)?;
            if let Some(compiled_class_hash) = compiled_class_hash {
                state.set_compiled_class_hash(class_hash, compiled_class_hash)?;
            }
            Ok(())
```

**File:** crates/blockifier/src/concurrency/worker_pool.rs (L139-163)
```rust
    fn _run_executor(&self, worker_executor: &WorkerExecutor<S>) {
        if self.a_thread_panicked.load(Ordering::Acquire) {
            panic!("Another thread panicked. Aborting.");
        }

        // Making sure that the program will abort if a panic occurred while halting
        // the scheduler.
        let abort_guard = AbortIfPanic;
        // If a panic is not handled or the handling logic itself panics, then we
        // abort the program.
        let res = panic::catch_unwind(panic::AssertUnwindSafe(|| {
            worker_executor.run();
        }));
        if let Err(err) = res {
            // First, set the panic flag. This must be done before halting the scheduler.
            self.a_thread_panicked.store(true, Ordering::Release);

            // If the program panics here, the abort guard will exit the program.
            // In this case, no panic message will be logged. Add the cargo flag
            // --nocapture to log the panic message.

            worker_executor.scheduler.halt();
            abort_guard.release();
            panic::resume_unwind(err);
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

**File:** crates/blockifier/src/execution/entry_point.rs (L706-734)
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

// Implementing the Drop trait to decrement the recursion depth when the guard goes out of scope.
impl Drop for RecursionDepthGuard {
    fn drop(&mut self) {
        *self.current_depth.borrow_mut() -= 1;
    }
}

```
