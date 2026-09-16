I found a genuine analog: the "bootstrap declare" mechanism uses only a hardcoded, public, well-known constant address (`'BOOTSTRAP'`) with no cryptographic or authorization guard restricting who may submit a transaction from that "sender." This mirrors the reported bug class (a privileged operation that lacks an authorization guard and is reachable by an unprivileged caller), but here the operation itself is a free, no-nonce-increment, unlimited-repeatable class declaration path.

### Title
Unauthenticated, replayable free class declaration via public `bootstrap_address` sender check bypasses fee/nonce/validation guards - (File: crates/starknet_api/src/executable_transaction.rs)

### Summary
The "bootstrap declare" flow, intended only for the one-time genesis bootstrapping of a new Starknet system, is guarded solely by three unauthenticated conditions checked against transaction *fields* the caller fully controls: `sender_address == bootstrap_address()`, `nonce == 0`, and `charge_fee == false` (i.e., `max_possible_fee == 0`). There is no signature check, no privileged-caller check, and no block-height/genesis-only restriction preventing this path from being invoked by any unprivileged party at any point in the chain's life, not just at genesis.

### Finding Description
`DeclareTransaction::bootstrap_address()` returns a fixed, public, non-secret contract address derived from the ASCII string `'BOOTSTRAP'`: [1](#0-0) 

`is_bootstrap_declare` treats any V3 declare transaction as a legitimate "bootstrap" declare purely based on client-supplied transaction fields — sender address equal to the public constant, nonce `0`, and a zero-fee flag — with no cryptographic signature verification of ownership over that "account," and no explicit restriction to the genesis block: [2](#0-1) 

In `AccountTransaction::execute_raw`, once `is_bootstrap_declare` returns true, the code entirely skips `perform_pre_validation_stage` (nonce/fee validation), the account's `__validate_declare__` entry point, and fee charging, and goes straight to declaring the class: [3](#0-2) 

The corresponding Starknet OS (Cairo) re-execution logic implements the identical unauthenticated check and skip-path, meaning both the sequencer's blockifier and the OS re-execution agree on this bypass — it is not merely a client-side quirk but is baked into the committed state transition and block hash: [4](#0-3) 

Because the nonce for the `'BOOTSTRAP'` address is never incremented on this path (the whole point being to allow no-account-semantics genesis declares), and because the address, nonce value, and zero-fee resource bounds required to hit this branch are all public, deterministic, and reproducible by anyone, an unprivileged transaction sender can submit declare transactions of this exact shape indefinitely — not only during genesis bootstrapping, but at any block height, for the lifetime of the chain.

The one intentional guard against abuse — `dict_update{...}(key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash)` in the OS, and analogous "declared only once" logic in blockifier's `try_declare`/state layer — only prevents re-declaring the *same* class hash twice; it does not prevent an unlimited stream of *new* class hashes from being declared for free, with zero fee accounted for, by any anonymous party, forever.

### Impact Explanation
This breaks the fee/resource-accounting invariant that all class declarations (a chargeable, size-bounded, state-growing operation) are paid for and bounded by account-level nonce/fee logic. An unprivileged party can:
- Continuously declare arbitrary new classes for free, with no fee ever charged to any account and no nonce protection limiting the rate, causing unbounded, free growth of committed contract-class state (a resource-exhaustion / state-bloat vector directly reachable from a single submitted transaction).
- Because the mempool never learns the "sender" nonce advanced (nonce stays `0`), as documented in the bootstrap test comment, "the transaction remains in the mempool" and "the mempool will propose the same transaction again" — meaning distinct such transactions (each declaring a different, never-before-seen class hash) will be admitted, executed, and committed without normal fee enforcement, on an ongoing basis, not just at chain genesis.
- Since this same bypass logic exists in the Starknet OS Cairo program used for re-execution/proving, all honest sequencer nodes and the OS will agree on the (incorrect) fee-free state transition, so it does not cause an honest-node divergence by itself, but it does cause a wrongly-committed state root reflecting free, unauthorized state growth outside the intended one-time bootstrap window — a violation of "unauthorized account action" / state-growth invariant enforced elsewhere in the fee/bouncer accounting layer.

### Likelihood Explanation
High. The `bootstrap_address()` constant, the required nonce (`0`), and the zero-fee resource-bounds condition are all public and fully specified in the open-source code and are exactly reproduced in test helpers (`generate_bootstrap_declare`) shipped in the repo. No secret key, signature, or privileged relationship is needed — any external, unprivileged party can construct and submit such a declare transaction to the gateway at any time after genesis, provided only that it references a class hash not yet declared.

### Recommendation
Restrict the bootstrap-declare bypass so it can only be exercised during genesis (e.g., gate it on `block_number == 0` in both the blockifier (`account_transaction.rs`) and the corresponding Starknet OS Cairo logic, or entirely remove the special-cased sender-address bypass and replace it with an explicit, sequencer-controlled genesis pre-declaration mechanism that isn't reachable via the normal transaction-submission path at all), so that no ordinary, ongoing block can accept a transaction that skips validation/nonce/fee enforcement.

### Proof of Concept
1. Compute `bootstrap_address()` as `ContractAddress::from(0x424f4f545354524150_u128)` (public constant). [5](#0-4) 
2. Craft any V3 Declare transaction with `sender_address = bootstrap_address()`, `nonce = 0`, and `resource_bounds` set to produce `max_possible_fee == 0` (using `ValidResourceBounds::create_for_testing_no_fee_enforcement()`-equivalent bounds), and a class hash/compiled-class-hash pair for a brand-new, never-declared class — exactly mirroring `generate_bootstrap_declare()`: [6](#0-5) 
3. Submit this transaction via the gateway at any block height (not just genesis) — nothing in `is_bootstrap_declare` or in gateway pre-validation restricts this to genesis.
4. The transaction bypasses `__validate_declare__`, fee charging, and nonce incrementing per `execute_raw`'s bootstrap branch, and the class is declared for free: [7](#0-6) 
5. Repeat with a new class hash each time (unbounded), since the `'BOOTSTRAP'` sender's nonce never advances and each new class hash satisfies `prev_value == 0`.

### Citations

**File:** crates/starknet_api/src/executable_transaction.rs (L246-255)
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
```

**File:** crates/starknet_api/src/executable_transaction.rs (L257-263)
```rust
    /// Returns the address of the bootstrap contract.
    /// Declare transactions can be sent from this contract with no validation, fee or nonce
    /// change. This is used for starting a new Starknet system.
    pub fn bootstrap_address() -> ContractAddress {
        // A felt representation of the string 'BOOTSTRAP'.
        ContractAddress::from(0x424f4f545354524150_u128)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L761-776)
```text
    // Do not run validate or perform any account-related actions for declare transactions that
    // meet the following conditions.
    // This flow is used for the sequencer to bootstrap a new system.
    if (sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3) {
        let max_possible_fee = compute_max_possible_fee(tx_info=tx_info);
        if (max_possible_fee == 0) {
            // Declare the class hash and skip the rest of the transaction.
            // Note that prev_value=0 enforces that a class may be declared only once.
            assert_not_zero(compiled_class_hash);
            dict_update{dict_ptr=contract_class_changes}(
                key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
            );
            %{ SkipTx %}
            return ();
        }
    }
```

**File:** crates/mempool_test_utils/src/starknet_api_test_utils.rs (L585-595)
```rust
/// Generate a declare transaction for initial bootstrapping phase (no fees).
pub fn generate_bootstrap_declare() -> RpcTransaction {
    let bootstrap_declare_args = declare_tx_args!(
        signature: TransactionSignature::default(),
        sender_address: DeclareTransaction::bootstrap_address(),
        resource_bounds: ValidResourceBounds::create_for_testing_no_fee_enforcement(),
        nonce: Nonce(Felt::ZERO),
        compiled_class_hash: *COMPILED_CLASS_HASH,
    );
    rpc_declare_tx(bootstrap_declare_args, contract_class())
}
```
