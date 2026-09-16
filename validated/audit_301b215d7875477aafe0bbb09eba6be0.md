### Title
Unauthenticated "BOOTSTRAP" Declare Bypass Allows Front-Running of Genesis Class Declarations - ([File: crates/starknet_api/src/executable_transaction.rs])

### Summary
The sequencer contains a hardcoded, permissionless bypass for `Declare` transactions sent from a fixed, publicly-known address (the ASCII literal `'BOOTSTRAP'`). Any transaction with `sender_address == bootstrap_address()`, `nonce == 0`, and no fee enforcement skips signature validation, `__validate_declare__` execution, and fee charging, and is accepted as a valid state mutation (declaring a class hash → compiled-class-hash mapping). This mirrors the Portainer bug class exactly: an unauthenticated/unauthorized action gated only by an implicit "system not yet initialized" precondition (here, the sequencer's bootstrap/genesis window) rather than actual identity verification.

### Finding Description
`DeclareTransaction::bootstrap_address()` returns a fixed constant, the felt encoding of the literal string `'BOOTSTRAP'` (`0x424f4f545354524150`), and `is_bootstrap_declare()` only checks the sender address, nonce, and the `charge_fee` flag — no signature, no key, no privileged credential: [1](#0-0) 

This predicate gates a full bypass of transaction authorization in the blockifier's execution path — no `perform_pre_validation_stage`, no `run_or_revert` (which normally invokes `__validate_declare__`), and no fee charging: [2](#0-1) 

The exact same special case is independently re-implemented in the Starknet OS Cairo program (used for proof/re-execution consensus), confirming that both the sequencer's execution path and the OS's re-execution path treat any `Declare` tx from this hardcoded address, with `nonce=0`, `version=3`, and zero max possible fee, as validated and executable without running `__validate_declare__`: [3](#0-2) 

The only mechanism that could plausibly block an ordinary attacker from constructing this transaction is the gateway's stateless resource-bounds check, which rejects zero-fee transactions **only if `config.validate_resource_bounds` is enabled**: [4](#0-3) 

During the bootstrap/genesis phase of any network built on this code (the documented purpose of this feature — "used for starting a new Starknet system"), this check must be relaxed/disabled to let the legitimate operator submit its own zero-fee bootstrap declarations. During that same window, the address, nonce, and version required to hit this code path are all public, hardcoded constants — there is no secret, key, or credential distinguishing "the operator's legitimate bootstrap tx" from an attacker's. Any party who can reach the gateway (a permissionless, public entry point by design in Starknet) can submit an equally-valid `Declare` transaction from `sender_address = bootstrap_address()`.

The state mutation performed uses `dict_update{...}(key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash)`, i.e., a strict "declare-once" guard: [5](#0-4) 

This means whichever `Declare` transaction targeting a given `class_hash` is admitted and executed first — attacker's or the legitimate operator's — permanently wins; the second one that tries to declare the same `class_hash` fails the `prev_value=0` assertion.

### Impact Explanation
An attacker who observes (or predicts) that a network is in its bootstrap window can race the legitimate operator's bootstrap declarations for well-known/expected class hashes (e.g., the canonical account contract class hash that the ecosystem expects to resolve to a specific, audited implementation). By submitting a `Declare` transaction from the `'BOOTSTRAP'` address with the *same* `class_hash` but an attacker-chosen `compiled_class_hash`/CASM, the attacker can:
- Permanently bind an expected/canonical `class_hash` to an attacker-controlled compiled class (unauthorized account action / wrong committed state), since the `prev_value=0` guard prevents any correction, or
- Cause the legitimate operator's real bootstrap declare to fail the `prev_value=0` assertion once the attacker's tx lands first, breaking genesis initialization and leaving the network unable to complete bootstrap (network unable to confirm new transactions / permanent freeze of the intended setup).

Both outcomes are concrete, permanent, and require no privileged access — exactly the class of harm the CWE-287/Portainer analog describes (authentication/authorization bypass gated by an initialization-state check instead of real identity verification).

### Likelihood Explanation
Exploitation requires only: (1) the target network being in its bootstrap phase (a normal, expected, recurring state for any new deployment based on this code, analogous to Portainer's 5-minute setup window), and (2) network access to submit a transaction to the gateway, which is permissionless by design. The `'BOOTSTRAP'` address, nonce, and version constants are hardcoded in the open-source repository, so no reconnaissance or credential theft is needed — any observer of a new network launch can race the operator's genesis declarations.

### Recommendation
Do not gate the bootstrap bypass solely on a public, hardcoded sender address and fee flag. Require an explicit, operator-controlled authorization for bootstrap declarations (e.g., a one-time governance/admin signature, an allow-listed genesis block number restriction enforced at both gateway and OS levels, or restricting the bypass to transactions embedded directly in the genesis block construction rather than accepted through the public gateway/mempool at any time the fee-enforcement flag happens to be relaxed).

### Proof of Concept
1. Deploy/observe a network based on this sequencer that is in its bootstrap phase (fee enforcement disabled for bootstrap declares, per `ValidResourceBounds::create_for_testing_no_fee_enforcement()`/equivalent production bootstrap config).
2. Craft a `DeclareTransaction::V3` with `sender_address = DeclareTransaction::bootstrap_address()` (`0x424f4f545354524150`), `nonce = 0`, `version = 3`, empty/default `signature`, and resource bounds that evaluate `enforce_fee(...) == false`, targeting the `class_hash` of a well-known contract the operator intends to declare during genesis, but supplying an attacker-chosen `compiled_class_hash`/CASM.
3. Submit this transaction to the gateway before the operator's legitimate bootstrap declare for the same `class_hash` is included.
4. Observe that the transaction is accepted with no signature check and no fee (per `AccountTransaction::execute_raw`'s bootstrap bypass), and the class hash mapping is committed via `dict_update` with `prev_value=0`, permanently locking in the attacker's mapping and causing the legitimate operator's later declare for the same class hash to fail.

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
