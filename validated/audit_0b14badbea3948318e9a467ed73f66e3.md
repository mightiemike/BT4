### Title
Unbounded recursion in `bytecode_hash_node` over declarer-controlled `NestedIntList` segment tree can stack-overflow the Sierra compiler service - (File: crates/starknet_api/src/contract_class/compiled_class_hash.rs)

### Summary
The compiled-class-hash algorithm recursively walks a `NestedIntList`/`HashableNestedIntList` tree (`bytecode_segment_lengths`) with no recursion-depth guard, mirroring the jq bug class described in CVE-2026-47770 (uncontrolled recursion over an attacker-influenced nested structure). This tree's shape is derived from the Sierra program submitted in a `DECLARE` transaction, i.e. it is influenced by an unprivileged contract declarer, not by an operator or peer.

### Finding Description
`bytecode_hash_node` recursively descends into the segment tree without any depth check: [1](#0-0) 

It is invoked from `bytecode_hash`, which is called from `CasmContractClass::hash` (called `.hash(&HashVersion::V2)`), which is used right after Sierra→CASM compilation for a declared class: [2](#0-1) 

Compare this to the deliberate `RecursionDepthGuard` used to bound *entry-point call* recursion in the execution engine: [3](#0-2) 

No equivalent guard exists for the `bytecode_hash_node` traversal. The `bytecode_segment_lengths` tree (`NestedIntList`) is produced by the Sierra-to-CASM compiler (`cairo_lang_starknet_classes`, an external dependency invoked from this codebase) based on the control-flow structure of the declared Sierra program; a program with deeply/pathologically nested control flow (bounded only by `max_contract_bytecode_size`, e.g. 81920 felts per `crates/apollo_node/resources/config_schema.json:3137-3141`) can produce a segment tree whose nesting depth is large enough to exhaust the compiler-thread stack when `bytecode_hash_node` recurses into it — the same "recursion repeating through" pattern cited for `jvp_array_equal`/`jv_cmp` in the CVE, just on a hash-computation path instead of an equality path.

I was **not able to fully verify** (within the index available to me) the exact maximum achievable nesting depth of the segment tree that the external `cairo_lang_starknet_classes` compiler produces for a given Sierra program, nor whether that crate itself already imposes a depth cap upstream. This repo's own analogous structure, `NestedFeltCounts`, explicitly asserts `segmentation_depth <= 1` (`crates/blockifier/src/execution/contract_class.rs:168`), suggesting the sequencer team is aware that unbounded segment nesting is a hazard for this general class of structure, but that constraint does not appear to be applied to `bytecode_hash_node`'s traversal of the raw `NestedIntList` returned by the compiler.

### Impact Explanation
A stack overflow in Rust from unbounded recursion causes an unrecoverable process abort (not a catchable `Result`/panic-with-unwind in the general case), which would crash the Sierra-compiler / class-manager component processing the declare transaction. If this recursion is deterministically reproducible from the submitted class bytes, every node attempting to compile/hash the same malicious class would crash identically, potentially repeatedly disrupting declare-transaction processing (and, depending on process boundaries, other services sharing the process) — a liveness/DoS impact.

### Likelihood Explanation
Likelihood is uncertain because it depends on facts I could not confirm from the available index: (1) the actual maximum nesting depth the upstream Sierra-to-CASM compiler can produce for a Sierra program within the `max_contract_bytecode_size` limit, and (2) the actual stack frame size of the monomorphized `bytecode_hash_node::<H, NL>` function (it uses `Itertools::collect_vec` and iterator chains, which are not usually cheap per frame). Without confirming these, I cannot assert with confidence that this is practically triggerable at CASM sizes permitted by the gateway.

### Recommendation
- Add an explicit recursion-depth guard (or convert `bytecode_hash_node` to an iterative, explicit-stack algorithm) in `crates/starknet_api/src/contract_class/compiled_class_hash.rs`, analogous to `RecursionDepthGuard` in `crates/blockifier/src/execution/entry_point.rs`.
- Enforce a maximum nesting depth on `NestedIntList`/`bytecode_segment_lengths` at Sierra-to-CASM compile time (mirroring the `segmentation_depth <= 1` invariant already enforced for `NestedFeltCounts` in `crates/blockifier/src/execution/contract_class.rs`), rejecting classes whose segment tree exceeds the bound during `apollo_compile_to_casm`'s `compile()` step.
- Add a regression/fuzz test that compiles a Sierra program engineered to maximize segment-tree nesting depth and asserts `hash()` completes without stack exhaustion.

### Proof of Concept
Not independently constructed/validated — building an actual pathological Sierra program that forces the upstream compiler to emit a deeply nested `NestedIntList` (and measuring the resulting stack usage of `bytecode_hash_node`) requires running the real `cairo_lang_starknet_classes` compiler and profiling stack depth, which is outside what I could do with the available read-only code search. This should be validated experimentally (e.g., via a Devin session with build/test tooling) before treating this as confirmed-exploitable rather than a plausible analog.

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
