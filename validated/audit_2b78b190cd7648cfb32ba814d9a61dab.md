### Title
`block_casm_hash_v1_declares` migration gate is never enforced for `DeclareTransaction::V2`, allowing declarers to keep registering classes with the deprecated Poseidon compiled-class hash - (File: crates/blockifier/src/transaction/transactions.rs)

### Summary
The Starknet protocol is migrating compiled-class hashing from the Poseidon-based "V1" algorithm to the Blake-based "V2" algorithm, and introduced the `block_casm_hash_v1_declares` versioned constant specifically to stop new declarations from registering classes under the old (deprecated) hash algorithm once the network decides to retire it. The enforcement check, `check_compile_class_hash_v2_declaration`, is only invoked for declare transactions whose version is `>= TransactionVersion::THREE`. `DeclareTransaction::V2` always carries `TransactionVersion::TWO`, so the gate is unconditionally skipped for it regardless of the `block_casm_hash_v1_declares` flag's value. Any class declarer can therefore keep submitting `DeclareTransactionV2` with a V1 (Poseidon) `compiled_class_hash` even after the network has flipped the flag to block exactly that.

### Finding Description
The enforcement logic lives in `DeclareTransaction::run_execute`: [1](#0-0) 

```
starknet_api::transaction::DeclareTransaction::V2(DeclareTransactionV2 { compiled_class_hash, .. })
| starknet_api::transaction::DeclareTransaction::V3(DeclareTransactionV3 { compiled_class_hash, .. }) => {
    if context.tx_context.block_context.versioned_constants.block_casm_hash_v1_declares
        && self.version() >= TransactionVersion::THREE
    {
        self.check_compile_class_hash_v2_declaration()?
    }
    try_declare(self, state, class_hash, Some(*compiled_class_hash))?
}
```

The `self.version() >= TransactionVersion::THREE` guard means the check is only performed for V3 declares. `DeclareTransactionV2` transactions always report `TransactionVersion::TWO` from `version()`, so the `&&` short-circuits and `check_compile_class_hash_v2_declaration` is never called for them — independent of whether `block_casm_hash_v1_declares` is `true`.

`check_compile_class_hash_v2_declaration` itself computes the CASM hash via the V2 (Blake) algorithm and requires the tx-supplied `compiled_class_hash` to match it, rejecting declares that supply the older Poseidon (V1) hash: [2](#0-1) 

Both the versioned-constants JSON history and diff regression file confirm this flag was turned on together with `enable_casm_hash_migration` in a real protocol upgrade (0.14.0 → 0.14.1), i.e., it is an intentional security/consistency gate meant to retire V1 declares network-wide: [3](#0-2) [4](#0-3) 

`HashVersion` and the dual-hash support are defined in: [5](#0-4) 

This is the closest reachable analog to the ClearanceKit CVE's root cause: the protocol wants to permanently retire acceptance of an older cryptographic artifact (V1/Poseidon compiled-class hash) via a "freshness"/version gate (`block_casm_hash_v1_declares`), but the gate's scope check (`self.version() >= TransactionVersion::THREE`) fails to cover the `DeclareTransaction::V2` code path that carries the exact same vulnerable field (`compiled_class_hash`), so a legitimately-formed (properly signed, properly hashed class-hash-wise) but policy-deprecated artifact — a V1-hashed declaration — can still be admitted indefinitely, i.e., "replayed" past the point the network intended to block it.

### Impact Explanation
Once `enable_casm_hash_migration`/`block_casm_hash_v1_declares` roll out, the network's intent is that all newly declared Cairo1 classes use the V2 (Blake) compiled-class-hash algorithm exclusively, presumably because the OS/Starknet migration path and future removal of Poseidon-hash support depend on this invariant. Because `DeclareTransaction::V2` bypasses the check entirely, any declarer can keep creating classes whose state entry holds the V1 hash after the cut-off. This:
- Undermines the migration guarantee that all classes are on V2 hashes post-migration, which other logic (`should_migrate`, `CasmHashMigrationData`, OS's `migrate_classes_to_v2_casm_hash`) relies on to eventually stop tracking/handling the V1 hash path.
- Creates inconsistent state across honest nodes if any future protocol version assumes V1 hashes are fully retired and removes the fallback/migration machinery — new V1-hash declarations made through this bypass would become unrepresentable/unverifiable, a form of honest-node divergence or unrecoverable state entries once V1 support is fully removed.
- Is only reachable via the declare-transaction path itself (any account/class declarer), matching the "declared class" reachable surface required by the prompt.

The severity is capped by the fact that `DeclareTransactionV2` is legacy (max_fee-based) and is expected to be phased out anyway, but as long as it remains accepted by the gateway/mempool/blockifier, the gate provides no actual protection against V1-hash declarations.

### Likelihood Explanation
High for reachability: submitting a `DeclareTransactionV2` with the class's Poseidon hash is a completely standard, unprivileged operation requiring no special permissions — any account can issue it as soon as `block_casm_hash_v1_declares` is enabled. No race condition or timing window is required (unlike the original CVE's opfilter-offline window); the bypass is deterministic and always available for this transaction version.

### Recommendation
Remove the `self.version() >= TransactionVersion::THREE` restriction (or otherwise extend the check to cover `DeclareTransaction::V2` as well) inside `DeclareTransaction::run_execute`, so that when `block_casm_hash_v1_declares` is active, any declare transaction with a Cairo1 class (`V2` or `V3`) is required to supply the V2 (Blake) compiled_class_hash, not just V3 ones. Add a regression test asserting that a `DeclareTransactionV2` carrying a V1-hash is rejected once the flag is enabled.

### Proof of Concept
1. Deploy/run a sequencer with versioned constants where `block_casm_hash_v1_declares = true` (e.g. 0.14.1 or later, as shown in `crates/blockifier/resources/blockifier_versioned_constants_0_14_1.json` line 123).
2. Compile a Cairo1 contract and compute both its V1 (Poseidon) and V2 (Blake) compiled class hashes, e.g. using `CasmContractClass::hash(&HashVersion::V1)` / `HashVersion::V2` as exercised in `crates/blockifier_test_utils/src/compile_cache.rs` lines 57-64.
3. Submit a `DeclareTransaction::V2` (`DeclareTransactionV2`) with `compiled_class_hash` set to the V1 (Poseidon) hash of the class, through the normal declare flow (`AccountTransaction::Declare` → `run_execute` in `crates/blockifier/src/transaction/transactions.rs`).
4. Observe that `self.version()` returns `TransactionVersion::TWO`, the `self.version() >= TransactionVersion::THREE` condition is `false`, `check_compile_class_hash_v2_declaration` is skipped, and `try_declare` proceeds to declare the class with the V1 hash recorded in state — despite `block_casm_hash_v1_declares` being enabled network-wide. Compare against the equivalent `DeclareTransaction::V3` case with the same V1 hash, which is correctly rejected with `DeclareTransactionCasmHashMissMatch` as demonstrated by the existing test `test_bootstrap_declare` (`crates/blockifier/src/transaction/account_transactions_test.rs`, lines 912-918).

### Citations

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

**File:** crates/blockifier/resources/versioned_constants_diff_regression/0.14.0_0.14.1.txt (L1-2)
```text
~ /block_casm_hash_v1_declares: true
~ /enable_casm_hash_migration: true
```

**File:** crates/blockifier/src/blockifier_versioned_constants.rs (L90-93)
```rust
    pub enable_reverts: bool,
    pub enable_casm_hash_migration: bool,
    pub block_casm_hash_v1_declares: bool,
    pub strip_vm_frames_in_sierra_gas: bool,
```

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L12-23)
```rust
/// Constant that defines the version of the compiled class hash algorithm.
pub static COMPILED_CLASS_V1: LazyLock<Felt> =
    LazyLock::new(|| Felt::from_bytes_be_slice(b"COMPILED_CLASS_V1"));

/// The version of the hash function used to compute the compiled class hash.
#[derive(Clone, Copy, PartialEq)]
pub enum HashVersion {
    /// Poseidon hash.
    V1,
    /// Blake2Felt252 hash.
    V2,
}
```
