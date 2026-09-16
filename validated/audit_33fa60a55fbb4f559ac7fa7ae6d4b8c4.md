## Analog Found

### Title
Bootstrap-declare bypass allows unpaid, unauthenticated class declarations at any block height - (File: `crates/starknet_api/src/executable_transaction.rs`)

### Summary
The reported bug is that an initialization routine mints protocol-privileged tokens (unbacked "genesis" liquidity) via a code path that skips the normal collateral/fee-accounting checks used by every other mint. The sequencer contains a directly analogous pattern: a hardcoded, non-deployed "magic" sender address (`'BOOTSTRAP'`) that, combined with `nonce == 0` and `charge_fee == false`, causes the transaction-execution layer to skip `__validate_declare__`, nonce incrementing, and fee charging altogether, and instead directly mutate committed state (declare an arbitrary class) for free. Nothing in the reachable execution path restricts this special-case treatment to genesis time — it is a pure function of transaction fields that any external submitter of a Declare v3 transaction fully controls.

### Finding Description
`DeclareTransaction::is_bootstrap_declare` treats any V3 declare transaction as a privileged "bootstrap" transaction purely based on transaction fields the sender fully controls: [1](#0-0) 

`bootstrap_address()` is not a real deployed/keyed account — it is simply the felt encoding of the ASCII string `"BOOTSTRAP"`, confirmed by the accompanying test: [2](#0-1) 

When the blockifier executes a Declare transaction whose `sender_address` equals this constant, `nonce == 0`, and the resource bounds are crafted so `charge_fee` is `false` (e.g. via `create_for_testing_no_fee_enforcement`/zero resource bounds), `execute_raw` takes a dedicated branch that entirely skips `perform_pre_validation_stage` (nonce/fee balance validation), skips `run_validate_entry_point`, and skips `handle_fee`, directly running only the declare's `run_execute` and returning a default (feeless) `TransactionExecutionInfo`: [3](#0-2) 

The Starknet OS program encodes the identical special case for re-execution/proving, again gated only on `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_possible_fee == 0` — with no block-number or one-time-use gate beyond "a class may be declared only once" (`prev_value=0`): [4](#0-3) 

The only "protection" against repeated abuse is that a given `class_hash` can only be declared once (the `dict_update` `prev_value=0` assertion), and the mempool comment notes the bootstrap sender's nonce is never incremented, so a rejected bootstrap-declare tx is simply dropped from the mempool after failing: [5](#0-4) 

This is functionally identical to the reported bug class: a privileged state mutation (there: minting tokens; here: declaring a class and permanently growing committed state) reachable through a path that bypasses the protocol's normal economic backing mechanism (fee payment, account validation, nonce management) — the only difference being what is "minted": here it is free, unauthenticated, permanently committed class declarations (Sierra→CASM compiled classes) rather than fungible tokens.

### Impact Explanation
Since `sender_address`, `nonce`, and `resource_bounds` are entirely attacker-controlled fields of a submitted RPC transaction, and there is no check in the reachable execution/validation code restricting this branch to block 0 or to a specific authorized proposer, any unprivileged party can submit a valid Declare v3 transaction with `sender_address = bootstrap_address()`, `nonce = 0`, and zero/`no_fee_enforcement` resource bounds for any not-yet-declared class hash. Each such transaction:
- Bypasses all fee accounting and resource-bound minimum checks that every other transaction must satisfy, undermining the fee market and bouncer's economic assumptions.
- Bypasses account `__validate_declare__` entirely — no signature/authorization check is performed for this "sender".
- Permanently commits new class declarations to global state at zero cost, an unbounded, free resource that can be repeated indefinitely (a new class hash can always be produced), enabling free growth of committed classes/CASM storage and free consumption of Sierra-to-CASM compilation and class-hashing work by the sequencer — resources that are supposed to be gated by fees/bouncer weights.
- Diverges from the intended one-time genesis-bootstrap semantics implied by the code comments ("used for starting a new Starknet system"), since nothing enforces that this path is exercised only once or only by the legitimate node operator.

### Likelihood Explanation
High reachability: the check is a pure, stateless function of transaction fields (`sender_address == 'BOOTSTRAP' && nonce == 0 && !charge_fee`) evaluated directly in the mainline `AccountTransaction::execute_raw` path used for every submitted transaction, and mirrored in the Starknet OS re-execution program. No signature verification, deployment check, or block-height gate exists in the inspected code to prevent an ordinary Declare v3 transaction (submitted through the gateway like any other transaction) from matching this branch after the chain has already produced blocks.

### Recommendation
Restrict the bootstrap-declare fast path so it can only be exercised during genuine genesis bootstrap — e.g., gate it on block number == 0 (or a dedicated "system not yet bootstrapped" flag persisted in state) in both `AccountTransaction::execute_raw` (`crates/blockifier/src/transaction/account_transaction.rs`) and the OS `execute_declare_transaction` (`transaction_impls.cairo`), and/or require an explicit one-time consumable "bootstrap allowed" state entry that is cleared after first use, so the free/unauthenticated declare path cannot be replayed by arbitrary senders at arbitrary block heights.

### Proof of Concept
1. Craft a `DeclareTransactionV3` RPC transaction with:
   - `sender_address = DeclareTransaction::bootstrap_address()` (the public constant `0x424f4f545354524150`, i.e. `'BOOTSTRAP'`),
   - `nonce = Nonce(Felt::ZERO)`,
   - `resource_bounds` such that `charge_fee` evaluates to `false` (e.g., `ValidResourceBounds::create_for_testing_no_fee_enforcement()`, as done by the existing helper `generate_bootstrap_declare` in `crates/mempool_test_utils/src/starknet_api_test_utils.rs:585-595`),
   - an arbitrary, not-yet-declared `class_hash`/`compiled_class_hash` for any valid contract class, and
   - `signature = TransactionSignature::default()` (no valid signature required).
2. Submit this transaction to the gateway like any ordinary user transaction.
3. `AccountTransaction::execute_raw` matches `tx.is_bootstrap_declare(charge_fee)`, skips validation/fee, and directly declares the class as shown in `crates/blockifier/src/transaction/account_transaction.rs:899-911`, producing a state diff that adds the class with zero fee paid and no nonce increment, exactly as demonstrated by the existing unit test `test_bootstrap_declare` (`crates/blockifier/src/transaction/account_transactions_test.rs:945-991`), which asserts "Ensure the only change is the class declaration: no fees, nonce bump, etc."
4. Repeat with new class hashes indefinitely across subsequent blocks to keep declaring classes for free, since the sender's nonce never advances and no other reachable check prevents reuse of the magic address after genesis.

**Uncertainty:** I could not fully verify within the available tool budget whether some higher-layer component (e.g., gateway or mempool configuration flags such as `allow_bootstrap_txs` referenced in the integration test) enforces an operational, config-level restriction preventing this path from being reachable in a production deployment after genesis. The core execution/validation logic itself (`account_transaction.rs`, `executable_transaction.rs`, and the OS Cairo program), however, contains no such restriction.

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

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L993-997)
```rust
#[test]
fn test_bootstrap_address() {
    let num = *ApiExecutableDeclareTransaction::bootstrap_address().0.key();
    assert_eq!("BOOTSTRAP", as_cairo_short_string(&num).unwrap());
}
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L888-911)
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

**File:** crates/apollo_integration_tests/tests/bootstrap_declare.rs (L19-21)
```rust
/// Bootstrap declare txs are unique: they are sent from a special address and do not increment its
/// nonce. As a result, they are not removed from the mempool upon successful execution, and will
/// only be removed after being rejected during a subsequent attempt.
```
