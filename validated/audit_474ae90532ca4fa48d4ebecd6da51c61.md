## Analysis

I looked for a sequencer analog to "redundant initialization logic that allows re-setting a privileged actor," and found a genuinely comparable pattern: the **BOOTSTRAP declare flow** for Declare-V3 transactions.

`DeclareTransaction::is_bootstrap_declare` treats a transaction as a special "system bootstrap" declare — skipping `__validate_declare__`, fee charging, and nonce incrementing — whenever three purely transaction-supplied conditions hold: sender address equals the hardcoded constant `bootstrap_address()` (`'BOOTSTRAP'`), `nonce == 0`, and `charge_fee == false`. [1](#0-0) 

This is consumed in `AccountTransaction::execute_raw`, which special-cases `Declare` transactions matching `is_bootstrap_declare`: it runs only `run_execute` (which declares the class), explicitly skips `perform_pre_validation_stage` (nonce/fee checks) and skips validation/fee-transfer entirely. [2](#0-1) 

However, on the actual declare-execution path, `try_declare` only allows this to succeed once per class hash — a class already declared cannot be redeclared (`StateError` → `DeclareTransactionError`) — and the OS-level implementation similarly enforces `prev_value=0` in the `dict_update` for `contract_class_changes`, i.e. each *class hash* can only be inserted once. [3](#0-2) [4](#0-3) 

I could not find any additional gating (e.g., a restriction that this flow may only execute at block 0, or that it can only be submitted by a privileged/internal component) beyond the sender address/nonce/fee-flag match on the transaction fields itself, nor a check that the "bootstrap" nonce is consumed after use (the code explicitly returns before `check_and_increment_nonce`, and comments in `account_transaction.rs` note that "the batcher does not notify the mempool" so "the mempool will propose the same transaction again" on the next block). This means, in principle, any user who can compose and submit a Declare-V3 transaction with `sender_address = 'BOOTSTRAP'`, `nonce = 0`, and `max_possible_fee = 0` could repeatedly attempt this fee-free, validation-free declare path against **any not-yet-declared class hash**, for as long as it is not rejected upstream — each attempt bypassing normal fee/nonce/account-validation gating. Because the `dict_update(prev_value=0)` uniqueness check is enforced per class hash rather than restricting the flow to a single genesis invocation, this is architecturally the same "redundant initialization gate" issue as the audit finding: a mechanism intended for one-time system bootstrap is reachable and repeatable by an ordinary transaction sender via ordinary transaction fields, rather than being cryptographically or structurally restricted to genesis/privileged initialization.

That said, I was not able to fully verify from the indexed code whether the gateway (`apollo_gateway`) or mempool independently rejects transactions with `sender_address == bootstrap_address()` before they reach execution — I did not find such a check in the stateful/stateless validator code I reviewed, but the index may not include every gateway validation path. If such a check exists elsewhere and unconditionally rejects any user-submitted transaction using the bootstrap address, this would not be independently exploitable and the finding would be moot. Given this residual uncertainty about full reachability, I present the finding below but flag this as the key open question for confirmation in a live session.

### Title
Bootstrap Declare Flow Reachable via Ordinary Transactions Bypasses Fee, Nonce, and Validation Checks - (File: crates/blockifier/src/transaction/account_transaction.rs)

### Summary
The `is_bootstrap_declare` mechanism, intended to let the sequencer declare the genesis account/ERC20 classes once during system bootstrap, is gated solely by transaction-supplied fields (`sender_address == 'BOOTSTRAP'`, `nonce == 0`, `charge_fee == false`) rather than by any privileged/internal-only invocation path.

### Finding Description
`DeclareTransaction::is_bootstrap_declare` returns true purely based on values present in an ordinary, user-constructible `DeclareTransactionV3` (address, nonce, fee flag). [1](#0-0) 
`AccountTransaction::execute_raw` branches on this predicate and, when true, skips `perform_pre_validation_stage` (nonce/fee checks), skips `__validate_declare__`, and skips fee charging — running only the raw declare execution. [2](#0-1) 
The mirrored Starknet OS Cairo code performs the identical shortcut, guarded only by `sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3` and `max_possible_fee == 0`. [4](#0-3) 
The only enforcement preventing repeated abuse is the per-class-hash `prev_value=0` uniqueness assertion in `try_declare`/`dict_update`, not any restriction limiting the bootstrap flow to a single genesis execution or to a privileged caller. [3](#0-2) 

### Impact Explanation
If reachable from ordinary transaction submission (unconfirmed — see caveat above), any transaction sender could declare arbitrary new classes for free repeatedly (once per not-yet-declared class hash), bypassing the fee mechanism, nonce anti-replay mechanism, and `__validate_declare__` entirely. This would let an attacker flood the sequencer with free declare transactions, causing unpriced resource consumption (compilation, storage of contract classes) and potential mempool/DoS pressure without paying fees — a bouncer/fee-accounting bypass reachable from a single submitted transaction.

### Likelihood Explanation
Likelihood depends entirely on whether upstream gateway/mempool validation independently rejects transactions using the reserved `bootstrap_address()`. I found no such explicit rejection in the code reviewed, but the index may not cover the complete gateway validation surface, so this is not conclusively proven.

### Recommendation
Restrict the bootstrap declare path so it cannot be triggered by any externally submitted transaction — e.g., reject any Declare transaction from `bootstrap_address()` in gateway/mempool ingestion paths, or require this flow to be injected only internally by the sequencer during genesis block construction (never accepted from the p2p/RPC ingestion surface), and enforce that this predicate cannot be satisfied for any block other than the genesis block.

### Proof of Concept
Not independently verified end-to-end due to inability to confirm whether `apollo_gateway` rejects `sender_address == bootstrap_address()` prior to execution; a concrete PoC would involve submitting a `DeclareTransactionV3` via the RPC/gateway with `sender_address = 0x424f4f545354524150` ('BOOTSTRAP'), `nonce = 0`, zero resource bounds (`max_possible_fee = 0`), and an arbitrary not-yet-declared class, and observing whether it executes through `is_bootstrap_declare`'s no-fee/no-validate/no-nonce-increment path.

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
