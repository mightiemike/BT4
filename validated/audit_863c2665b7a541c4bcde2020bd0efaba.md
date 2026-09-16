## Title
Unrestricted reuse of the `'BOOTSTRAP'` declare bypass allows unprivileged, fee‑free, unlimited class declarations after genesis — (File: `crates/starknet_api/src/executable_transaction.rs`, `crates/blockifier/src/transaction/account_transaction.rs`, `crates/apollo_starknet_os_program/.../transaction_impls.cairo`)

### Summary
The sequencer implements a special "bootstrap declare" code path intended only for initializing a brand‑new Starknet system before any account exists to pay fees. The path is gated solely by `sender_address == 'BOOTSTRAP' && nonce == 0 && version == 3 && no-fee-enforcement`, with no check that the chain is actually at genesis (block height 0). Because the `'BOOTSTRAP'` address is never a deployed account, its nonce permanently reads as `0`, so this "genesis-only" bypass remains callable by any unprivileged transaction sender for the lifetime of the chain, letting them declare arbitrary classes for free, repeatedly, while skipping all account validation, nonce incrementing, and fee charging.

### Finding Description
`DeclareTransaction::is_bootstrap_declare` determines the bypass purely from transaction fields, not from chain state/height: [1](#0-0) 

This flag is consumed in `AccountTransaction::execute_raw`, which — when true — skips `perform_pre_validation_stage` (nonce/fee/balance checks) entirely and runs only `run_execute`: [2](#0-1) 

The actual declaration in the Starknet OS program mirrors this: it checks `sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3` and, if the computed max possible fee is `0`, directly writes the class hash into `contract_class_changes` with `prev_value=0` (so only a not-yet-declared class hash can be targeted) and skips the rest of transaction processing — no genesis/height gate exists here either: [3](#0-2) 

The `'BOOTSTRAP'` address is a synthetic constant, not a deployed/controlled account: [4](#0-3) 

Since no contract is ever deployed at that address, `get_nonce_at` for it will always return the default nonce (`0`) for the entire life of the chain (this is also implied by the code comment noting the tx "does not increment its nonce" and remains re‑usable in the mempool): [5](#0-4) 

The only state safeguard against repeated abuse is the `prev_value=0` requirement on `contract_class_changes`, which prevents redeclaring the *same* class hash — but does **not** prevent an attacker from submitting new bootstrap-declare transactions with different (unique/never‑declared) class hashes indefinitely, at any block height, well after genesis.

This is structurally analogous to the OUSD `H01` finding: a "one-time system bootstrap" operation is gated by an easily-satisfied, permanently-true condition (a flag/state check meant to be transient) rather than being properly restricted to the intended one-time initialization window, letting an unprivileged actor repeatedly re-trigger privileged/free behavior.

### Impact Explanation
Any unprivileged party can submit `Declare` V3 transactions with `sender_address = 'BOOTSTRAP'`, `nonce = 0`, and zero/no-fee resource bounds, targeting any not-yet-declared class hash, at any point after genesis. Each such transaction:
- Declares a class with **zero fee** (bypassing the fee/resource-accounting mechanism entirely), and
- Bypasses `__validate_declare__`, nonce increment, and balance checks.

This allows unbounded, free class declarations, which can be used to flood block space with declare-transaction bouncer weight/DA at no cost to the attacker (economic DoS / fee-accounting bypass), undermining the sequencer's fee model and bouncer resource accounting invariants for an unprivileged, permanently-reachable path.

### Likelihood Explanation
High. The trigger requires only crafting a normal V3 `Declare` transaction with a specific sender address, nonce 0, and zero-fee resource bounds — all fields controllable by any user submitting to the gateway/mempool. No special privileges, timing (other than "not yet declared class hash"), or race conditions are needed; the condition (`nonce==0` for an undeployed address) holds forever.

### Recommendation
Restrict the bootstrap-declare bypass so it can only be exercised while the chain has no blocks yet (i.e., true genesis), for example by having the gateway/mempool/batcher reject `'BOOTSTRAP'`-sender declare transactions once `latest_block_number` is `Some(_)`, and/or by tracking an explicit one-time "bootstrap completed" flag in state (set atomically, with proper access control) rather than relying solely on `nonce == 0` of a never-deployed address.

### Proof of Concept
1. After the chain has produced at least one block (i.e., genesis is over), craft a `DeclareTransaction::V3` with:
   - `sender_address = DeclareTransaction::bootstrap_address()` (`0x424f4f545354524150`),
   - `nonce = Nonce(Felt::ZERO)`,
   - `resource_bounds` set for no fee enforcement (as done in `generate_bootstrap_declare`): [6](#0-5) 
   - `class_hash`/`compiled_class_hash` for any not-yet-declared class.
2. Submit it via the gateway like a normal transaction.
3. `is_bootstrap_declare` returns `true` (nonce is still `0` because the address was never deployed), so `execute_raw` skips validation/fee stages and directly declares the class for free: [7](#0-6) 
4. Repeat with a new class hash to declare unlimited classes for free at any block height, not just at genesis.

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
