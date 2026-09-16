### Title
Unauthenticated, unlimited free `Declare` via the fixed "BOOTSTRAP" sender address bypass - ([File: crates/starknet_api/src/executable_transaction.rs], [File: crates/blockifier/src/transaction/account_transaction.rs])

### Summary
The bootstrap-declare fast path lets a `Declare` transaction skip nonce checks, `__validate__` (signature verification), and fee charging entirely whenever `sender_address == bootstrap_address()`, `nonce == 0`, and the transaction's own resource bounds compute to zero fee. All three of these conditions are attacker-controlled fields of an ordinary V3 `Declare` transaction, so any unprivileged sender can repeatedly submit "free" declarations without ever proving ownership of any key for that address — directly analogous to the CVE-2025-58060 pattern where a non-Basic auth type still let a bogus `Authorization: Basic` header skip the password check because the code path that should gate access on identity was bypassed based on attacker-influenced state rather than a real credential check.

### Finding Description
`DeclareTransaction::is_bootstrap_declare` treats a transaction as a "bootstrap declare" purely based on transaction content: [1](#0-0) 

```
pub fn is_bootstrap_declare(&self, charge_fee: bool) -> bool {
    if let crate::transaction::DeclareTransaction::V3(tx) = &self.tx {
        return tx.sender_address == Self::bootstrap_address()
            && tx.nonce == Nonce(Felt::ZERO)
            && !charge_fee;
    }
    false
}
```

`bootstrap_address()` is a fixed, publicly known constant (`0x424f4f545354524150`, the ASCII value of "BOOTSTRAP"), not a real deployed/controlled account: [2](#0-1) 

`charge_fee` is derived from `enforce_fee`, which is computed purely from the transaction's own `resource_bounds`/`tip` fields — both of which the sender fully controls: [3](#0-2) [4](#0-3) 

In `AccountTransaction::execute_raw`, when `is_bootstrap_declare` returns true, the code entirely skips `perform_pre_validation_stage` (nonce/fee checks) **and** the normal validate/execute/fee flow, running only `tx.run_execute`, then returns immediately: [5](#0-4) 

Critically, because `perform_pre_validation_stage` (which increments the nonce) is skipped, the sender's nonce at the bootstrap address is never advanced. There is no `__validate__` call (no signature check of any kind is performed — the account at this address need not exist, need not have any code, and the "sender" needs no private key at all), and no fee is ever charged.

The only gate that a re-declaration of the *same* class hash fails is in `try_declare`, which simply errors if the class hash is already declared: [6](#0-5) 

This does not prevent the attacker from picking a fresh, arbitrary class per submission, so the "bootstrap declare" path can be invoked unboundedly by anyone — not just once during genesis bootstrapping as the design comment implies: [7](#0-6) 

Gateway-side stateless validation only rejects **zero** resource bounds when `validate_resource_bounds` is enabled (the default), via `ZeroResourceBounds`: [8](#0-7) 
But `enforce_fee`/`charge_fee=false` does not require *all-zero* bounds — it only requires `resource_bounds.max_possible_fee(tip) == 0`, and the gateway's `min_gas_price` check only bounds `l2_gas.max_price_per_unit`, not enforcing the exact combination needed to keep `max_possible_fee` at zero while still passing the non-zero-bounds check. Existing unit tests explicitly cover the `!charge_fee` scenario, confirming this is triggerable with attacker-chosen resource bounds: [9](#0-8) 

### Impact Explanation
Any unprivileged sender can submit V3 `Declare` transactions with `sender_address = bootstrap_address()`, `nonce = 0`, and resource bounds engineered so `max_possible_fee(tip) == 0`, while still passing the gateway's non-zero resource-bounds stateless check (e.g., using an asymmetric combination of resource types/tip so the aggregate max possible fee still computes to zero under `enforce_fee`, or via any node/config where `validate_resource_bounds` is disabled). Each such transaction:
- Skips nonce enforcement (nonce never increments for that "account"), so the exact same bypass can be replayed indefinitely with distinct class hashes.
- Skips `__validate__`/signature verification entirely — an unauthorized action (declaring arbitrary Sierra classes into global state) is performed without any authentication.
- Skips fee charging — free, unbounded compute/storage consumption by the sequencer (class compilation, CASM hash computation, state writes), leading to unbounded resource consumption without payment.

This is a concrete "unauthorized account action" (arbitrary class declarations attributed to an account nobody controls, bypassing the account-abstraction authentication model that Starknet relies on for all account actions) reachable from a single unprivileged transaction, matching a Medium/High severity class per the validation rules (unauthorized account action bypassing authentication, reachable by any transaction sender).

### Likelihood Explanation
High. No special privileges, no key material for `bootstrap_address()`, and no coordination with a validator/proposer are required — a normal RPC/gateway submission from any external client suffices. The relevant condition checks (`sender_address`, `nonce`, and resource-bounds-derived `charge_fee`) are all directly attacker-supplied transaction fields, and the constant `bootstrap_address()` is derivable from public source code.

### Recommendation
Restrict the bootstrap-declare fast path so it can only apply once, pre-genesis (e.g., gate it on block number == 0 / state being empty, rather than solely on transaction content), or remove the implicit trust in `sender_address == bootstrap_address()` combined with attacker-controlled `charge_fee`. At minimum, always run standard nonce enforcement (so nonce=0 cannot be replayed) even on the bootstrap path, and require that this path only be reachable when the addressed "account" contract has non-trivial system-level authorization (e.g., restrict to a node-local bootstrapping flow rather than a path reachable through the public transaction-submission pipeline).

### Proof of Concept
1. Craft a `DeclareTransaction::V3` with:
   - `sender_address = ContractAddress(0x424f4f545354524150)` (the literal `bootstrap_address()` value).
   - `nonce = Nonce(Felt::ZERO)`.
   - `resource_bounds`/`tip` chosen so that `ValidResourceBounds::max_possible_fee(tip) == Fee(0)` (per `enforce_fee`) while still not failing the gateway's `ZeroResourceBounds` stateless check (which only rejects the fully-zero case) — e.g., relying on `validate_resource_bounds` gateway config being disabled, or picking a bounds/tip combination where the aggregate possible fee is zero under `enforce_fee` semantics.
   - `class_hash`/`compiled_class_hash`/contract class set to an arbitrary attacker-chosen Sierra/CASM class, with `check_compile_class_hash_v2_declaration` satisfied.
2. Submit the transaction to the gateway. `AccountTransaction::new_for_sequencing` sets `charge_fee = enforce_fee(&tx, false) = false`.
3. In `execute_raw`, `tx.is_bootstrap_declare(false)` evaluates true → `perform_pre_validation_stage`, `__validate__`, and fee charging are all skipped; `tx.run_execute` directly declares the class.
4. Repeat with a new class hash each time (nonce never advances) to declare unlimited classes for free with no signature ever checked, at zero cost — demonstrating unauthorized, unauthenticated, unbounded state mutation on the network.

(Note: the exact resource-bounds combination that simultaneously satisfies "non-zero per the stateless `ZeroResourceBounds` check" and "`enforce_fee` == false" depends on precise `max_possible_fee` arithmetic across L1/L2/L1-data-gas and tip, which was not fully re-derived here from the code available in this index; a background Devin session with full repo access should verify the exact numeric combination or confirm whether `validate_resource_bounds` being disabled in some deployed gateway configs is sufficient on its own.)

### Citations

**File:** crates/starknet_api/src/executable_transaction.rs (L246-263)
```rust
    // Returns whether the declare transaction is for bootstrapping.
    // In this case, no account-related actions should be made besides the declaration.
    pub fn is_bootstrap_declare(&self, charge_fee: bool) -> bool {
        if let crate::transaction::DeclareTransaction::V3(tx) = &self.tx {
            return tx.sender_address == Self::bootstrap_address()
                && tx.nonce == Nonce(Felt::ZERO)
                && !charge_fee;
        }
        false
    }

    /// Returns the address of the bootstrap contract.
    /// Declare transactions can be sent from this contract with no validation, fee or nonce
    /// change. This is used for starting a new Starknet system.
    pub fn bootstrap_address() -> ContractAddress {
        // A felt representation of the string 'BOOTSTRAP'.
        ContractAddress::from(0x424f4f545354524150_u128)
    }
```

**File:** crates/blockifier/src/transaction/objects.rs (L105-113)
```rust
    pub fn enforce_fee(&self) -> bool {
        match self {
            TransactionInfo::Current(context) => {
                // Assumes that the tip is enabled, as it is in the OS.
                context.resource_bounds.max_possible_fee(context.tip) > Fee(0)
            }
            TransactionInfo::Deprecated(context) => context.max_fee != Fee(0),
        }
    }
```

**File:** crates/blockifier/src/transaction/transactions.rs (L379-383)
```rust
/// Determines whether the fee should be enforced for the given transaction.
pub fn enforce_fee(tx: &AccountTransaction, only_query: bool) -> bool {
    // TODO(AvivG): Consider implemetation without 'create_tx_info'.
    tx.create_tx_info(only_query).enforce_fee()
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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L888-912)
```rust
        // Do not run validate or perform any account-related actions for declare transactions that
        // meet the following conditions.
        // This flow is used for the sequencer to bootstrap a new system.
        // Note: The absence of any account-related action leads to some unintuitive but expected
        // behavior:
        // - After the transaction is executed successfully, the batcher does not notify the mempool
        //   about its inclusion in a block. As a result, the transaction remains in the mempool.
        // - When the next block is produced, the mempool will propose the same transaction again.
        // - This time, execution will fail because the contract has already been declared.
        // - The transaction will then be marked as rejected, the mempool will be notified, and the
        //   transaction will be removed from the mempool.
        if let Transaction::Declare(tx) = &self.tx {
            if tx.is_bootstrap_declare(self.execution_flags.charge_fee) {
                let mut context = EntryPointExecutionContext::new_invoke(
                    tx_context.clone(),
                    self.execution_flags.charge_fee,
                    SierraGasRevertTracker::new(GasAmount::default()),
                );
                let mut remaining_gas = 0;
                let res = tx.run_execute(state, &mut context, &mut remaining_gas)?;
                assert!(res.is_none(), "Declare execute should not result in a CallInfo.");

                return Ok(TransactionExecutionInfo::default());
            }
        }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-69)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }
```

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L905-944)
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
#[should_panic(expected = "UninitializedStorageAddress")]
#[case::wrong_tx_version(DeclareTransaction::V2(DeclareTransactionV2 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    ..Default::default()
}), HashVersion::V2)]
#[should_panic(expected = "InvalidNonce")]
#[case::wrong_nonce(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    nonce: Nonce(felt!(1_u64)),
    ..Default::default()
}), HashVersion::V2)]
#[should_panic(expected = "UninitializedStorageAddress")]
#[case::wrong_sender_address(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ContractAddress(PatriciaKey::from(1_u128)),
    ..Default::default()
}), HashVersion::V2)]
#[should_panic(expected = "InsufficientResourceBounds")]
#[case::non_trivial_resource_bounds(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    resource_bounds: ValidResourceBounds::AllResources(AllResourceBounds {
        l1_gas: ResourceBounds::default(),
        l2_gas: ResourceBounds{max_amount: GasAmount(1), max_price_per_unit: GasPrice(1)},
        l1_data_gas: ResourceBounds::default(),
    }),
    ..Default::default()
}), HashVersion::V2)]
```
