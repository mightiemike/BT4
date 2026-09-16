## Finding: Unbounded Recursion in `bytecode_hash_node` Causes Stack-Overflow DoS When Hashing a Declared Class's CASM Bytecode Segments

### Title
Uncontrolled Recursion in `bytecode_hash_node` Leads to Stack Overflow via Deeply-Nested `bytecode_segment_lengths` During Declared-Class Compiled-Class-Hash Verification - (File: `crates/starknet_api/src/contract_class/compiled_class_hash.rs`)

### Summary
Computing the compiled-class-hash of a Sierra→CASM–compiled contract recursively walks the `bytecode_segment_lengths` tree (`NestedIntList`) with no depth bound, exactly like ImageMagick's `DestroyXMLTree` recursing an XML tree with no depth limit. A class declarer can submit a Cairo1 contract whose Sierra control-flow structure forces the compiler to emit a `NestedIntList` with a nesting depth proportional to program size, and the subsequent hash computation will stack-overflow the sequencer/compiler process — an unrecoverable process abort in Rust (unlike a catchable panic).

### Finding Description
`bytecode_hash_node` recurses once per nesting level of the `HashableNestedIntList`/`NestedIntList` tree with no maximum-depth check: [1](#0-0) 

This tree, `bytecode_segment_lengths`, is produced by the external Sierra-to-CASM compiler (`cairo-lang-starknet-classes`) based on the segmentation of the *declarer-supplied* Sierra program's control-flow (branches/functions). It is not attacker-supplied JSON directly, but its shape is derived from and controllable via the Sierra bytecode the declarer submits.

This hash function is invoked on the class-declaration path in at least two reachable places:
1. Right after Sierra→CASM compilation, before the compiled class is even accepted, inside the sequencer's compilation service: [2](#0-1) 
2. During `Declare` transaction execution/validation, to check that the caller-supplied `compiled_class_hash` matches the recomputed CASM hash: [3](#0-2) 
called from `DeclareTransaction::run_execute`: [4](#0-3) 

Both paths are reached from a single, unprivileged, user-submitted `Declare` transaction (v2/v3) with no privileged actor required.

### Impact Explanation
A stack overflow in Rust aborts the process (SIGABRT/segfault) rather than raising a catchable `Result`/panic that the surrounding `?`-based error handling can absorb. Triggering this from:
- the `apollo_compile_to_casm`/`SierraCompiler` component crashes the sequencer's Sierra-compilation service, blocking declaration (and therefore block building/validation) of any class until restart;
- the `Declare` transaction execution path (`try_declare` → `check_compile_class_hash_v2_declaration`) crashes the executing sequencer/validator process mid-block-building, since it is called unconditionally for every V2/V3 declare with `block_casm_hash_v1_declares` enabled.

Either crash halts a node's ability to process further transactions/blocks — matching the "network unable to confirm new transactions" bar, since block builders/sequencers running this code path go down on receipt of a single malicious declare transaction.

### Likelihood Explanation
Any account can submit a `Declare` transaction. Crafting a Cairo1 contract whose Sierra program has many nested branches (deeply nested `if`/`match` chains) is achievable with ordinary Cairo source and does not require special privileges — it only needs to pass compiler size/step limits (`max_bytecode_size` etc.), which bound total instruction count but not segmentation nesting depth. Since each level of nesting in `bytecode_segment_lengths` requires only one child node (not two), an attacker can construct a program whose segment tree depth is proportional to the bytecode size, driving stack usage up linearly with contract size — well within limits used in production (`DEFAULT_MAX_BYTECODE_SIZE`).

### Recommendation
Convert `bytecode_hash_node` (and the underlying tree traversal in `HashableNestedIntList`) to an explicit, heap-allocated worklist/stack-based iterative algorithm instead of native call-stack recursion, or impose and enforce a hard maximum nesting depth for `NestedIntList`/`bytecode_segment_lengths` before hashing, rejecting classes that exceed it during compilation/validation.

### Proof of Concept
1. Author (or programmatically generate) a Cairo1 contract whose body consists of `N` sequentially nested `if`/`match` branches (e.g., ~50,000+ levels), keeping total bytecode under `max_bytecode_size`.
2. Compile it to Sierra and submit as the `contract_class` of a `Declare` V3 transaction with an arbitrary `compiled_class_hash`.
3. When the sequencer compiles the Sierra to CASM (`apollo_compile_to_casm::SierraCompiler::compile`) and computes `executable_class.hash(&HashVersion::V2)`, or when `DeclareTransaction::check_compile_class_hash_v2_declaration` recomputes the hash during execution, `bytecode_hash_node` recurses to depth `N`, exhausting the thread stack and aborting the process.

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

**File:** crates/apollo_compile_to_casm/src/lib.rs (L66-69)
```rust
        // TODO(Elin): handle resources (whether here or an infra. layer load-balancing).
        let executable_class = self.compiler.compile(class)?;
        // TODO(Elin): consider spawning a worker for hash calculation.
        let executable_class_hash = executable_class.hash(&HashVersion::V2);
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

**File:** crates/blockifier/src/transaction/transactions.rs (L176-190)
```rust
            starknet_api::transaction::DeclareTransaction::V2(DeclareTransactionV2 {
                compiled_class_hash,
                ..
            })
            | starknet_api::transaction::DeclareTransaction::V3(DeclareTransactionV3 {
                compiled_class_hash,
                ..
            }) => {
                if context.tx_context.block_context.versioned_constants.block_casm_hash_v1_declares
                    && self.version() >= TransactionVersion::THREE
                {
                    self.check_compile_class_hash_v2_declaration()?
                }
                try_declare(self, state, class_hash, Some(*compiled_class_hash))?
            }
```
