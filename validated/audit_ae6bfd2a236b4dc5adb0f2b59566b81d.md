### Title
Declare-transaction `compiled_class_hash` accepted without cryptographic verification against the actual CASM hash for V2 declares (and gated only conditionally for V3) - (File: crates/blockifier/src/transaction/transactions.rs)

### Summary
The reported RustDesk issue is that a client accepts a "config" value that is supposed to be cryptographically bound to its content, without actually validating that binding (no hash/signature check), letting an attacker-controlled value pass as trusted data. The closest reachable analog in this sequencer is the handling of the `compiled_class_hash` field on `Declare` transactions: the field is meant to be a cryptographic commitment (hash) of the CASM contract class, but the blockifier only verifies this commitment for a subset of transaction versions, and stores the caller-supplied value into state as ground truth otherwise.

### Finding Description
`DeclareTransaction::run_execute` in [1](#0-0)  only calls `check_compile_class_hash_v2_declaration()` — the function that actually recomputes `casm.hash(&HashVersion::V2)` and compares it against the transaction-supplied `compiled_class_hash` (see [2](#0-1) ) — under the condition `block_casm_hash_v1_declares && self.version() >= TransactionVersion::THREE`.

For `DeclareTransactionV2` (or any V3 declare when the `block_casm_hash_v1_declares` versioned-constant flag is not enabled), execution proceeds directly to `try_declare`, which writes the attacker/user supplied `compiled_class_hash` into state via `state.set_compiled_class_hash` with no verification that it corresponds to the actual hash of the declared CASM bytecode: [3](#0-2) 

This means the `class_hash_to_compiled_class_hash` mapping committed to state (and ultimately to the Patricia tree / state root) can contain a `compiled_class_hash` value that is not the true cryptographic hash of the associated CASM contract — i.e., a "pseudo-verified" hash is accepted without being cryptographically checked, exactly mirroring the reported bug class ("accepts pseudo-encrypted/pseudo-verified data without cryptographic validation").

### Impact Explanation
If the compiled_class_hash committed to state does not match the real hash of the CASM, this creates a discrepancy between what the sequencer's blockifier commits to the state tree and what any honest re-execution (e.g., Starknet OS / SNOS) would independently compute when it hashes the same CASM (see `blake_compiled_class_hash` / `validate_compiled_class_facts` in the OS Cairo code, [4](#0-3) , which asserts `compiled_class_fact.hash = hash`). This can lead to: (a) wrong committed state root/class commitment if the discrepancy is not independently caught at the OS layer for this code path, or (b) an inconsistency between blockifier-accepted blocks and OS-provable blocks, which is a form of honest-node/consensus divergence between execution and proving layers. I was not able to fully confirm within the available iterations whether the SNOS re-execution path independently re-derives and asserts the V1-hash-equivalent commitment for all declare versions (V2 in particular), so I cannot conclusively state whether the OS layer always catches this before commitment — this is an open point requiring further code tracing.

### Likelihood Explanation
This is reachable directly by any account submitting an ordinary `Declare` V2 transaction (or a V3 transaction when the `block_casm_hash_v1_declares` flag has not yet been activated for the current protocol/versioned-constants set) — no special privileges, no operator/proposer/peer compromise required. The check exists and is clearly guarded behind a versioned-constants flag and a version check, suggesting the gap is either an intentional, gradual protocol rollout (V1→V2 hash migration) or an incomplete validation. Given the explicit conditional gating (`self.version() >= TransactionVersion::THREE`), V2 declares appear to be unconditionally exempt from this hash-commitment check in the current code.

### Recommendation
- Verify that `compiled_class_hash` is checked against the actual computed hash of the CASM contract class for all Cairo1 declare versions (V2 and V3), not just V3 transactions gated by `block_casm_hash_v1_declares`.
- If V2 is intentionally deprecated/legacy and the corresponding hash algorithm (V1) is still supported for backward compatibility, ensure `check_compile_class_hash_v2_declaration` (or an equivalent V1-hash check) is invoked with the version-appropriate hash algorithm rather than being skipped entirely for V2.
- Add a regression/property test asserting that no `Declare` transaction (of any accepted version) can be executed and committed to state with a `compiled_class_hash` that does not match the corresponding recomputed hash of the CASM contract for the hash version associated with that transaction version.

### Proof of Concept
1. Craft a valid Cairo1 `DeclareTransactionV2` (or a V3 transaction on a network configuration where `block_casm_hash_v1_declares` is `false`) with a legitimate, compilable CASM `contract_class`, but set `compiled_class_hash` to an arbitrary, unrelated felt value (not equal to `casm.hash(...)`).
2. Submit the transaction through the gateway; observe that `DeclareTransaction::run_execute` ( [5](#0-4) ) skips `check_compile_class_hash_v2_declaration` for this version, and `try_declare` ( [6](#0-5) ) commits the bogus `compiled_class_hash` into state via `state.set_compiled_class_hash`.
3. Confirm (as already demonstrated by the test at [7](#0-6) , which shows the mismatch is only caught for the `HashVersion::V1` case under the `poseidon_declare_tx` V3 scenario) that no equivalent panic/rejection occurs for a V2 declare transaction with mismatched `compiled_class_hash`, and that `state.get_compiled_class_hash(class_hash)` returns the attacker-chosen, unverified value.

### Citations

**File:** crates/blockifier/src/transaction/transactions.rs (L155-193)
```rust
impl<S: State> Executable<S> for DeclareTransaction {
    fn run_execute(
        &self,
        state: &mut S,
        context: &mut EntryPointExecutionContext,
        _remaining_gas: &mut u64,
    ) -> TransactionExecutionResult<Option<CallInfo>> {
        let class_hash = self.class_hash();
        match &self.tx {
            starknet_api::transaction::DeclareTransaction::V0(_)
            | starknet_api::transaction::DeclareTransaction::V1(_) => {
                if context.tx_context.block_context.versioned_constants.disable_cairo0_redeclaration
                {
                    try_declare(self, state, class_hash, None)?
                } else {
                    // We allow redeclaration of the class for backward compatibility.
                    // In the past, we allowed redeclaration of Cairo 0 contracts since there was
                    // no class commitment (so no need to check if the class is already declared).
                    state.set_contract_class(class_hash, self.contract_class().try_into()?)?;
                }
            }
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
        }
        Ok(None)
    }
```

**File:** crates/blockifier/src/transaction/transactions.rs (L385-408)
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
        }
        Err(error) => Err(error)?,
        Ok(_) => {
            // Class is already declared, cannot redeclare.
            Err(TransactionExecutionError::DeclareTransactionError { class_hash })
        }
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo (L97-132)
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

```

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L905-918)
```rust
#[rstest]
#[case::valid(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    class_hash: class_hash!(7_u64),
    compiled_class_hash: CompiledClassHash(8_u64.into()),
    ..Default::default()
}), HashVersion::V2)]
#[should_panic(expected = "DeclareTransactionCasmHashMissMatch")]
#[case::poseidon_declare_tx(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    class_hash: class_hash!(7_u64),
    compiled_class_hash: CompiledClassHash(8_u64.into()),
    ..Default::default()
}), HashVersion::V1)]
```
