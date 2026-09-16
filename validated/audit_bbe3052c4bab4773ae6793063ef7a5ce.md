### Title
Bootstrap-Declare Path Allows Any Unprivileged Sender to Declare Classes Without Signature Validation or Fee Payment - ([File: crates/starknet_api/src/executable_transaction.rs])

### Summary
The "bootstrap declare" fast-path, intended only for a chain's genesis initialization, is gated solely by three transaction fields that are fully controlled by the transaction author: `sender_address == 'BOOTSTRAP'`, `nonce == 0`, and a zero-fee resource-bounds configuration. There is no additional authorization check (e.g. restricting the path to the genesis block, a privileged submitter, or a one-time system flag). Any ordinary, unauthenticated network client can therefore craft a `Declare` transaction that hits this path at any point in the chain's lifetime, causing the sequencer to skip `__validate_declare__` and fee charging entirely and declare an arbitrary class.

### Finding Description
`DeclareTransaction::is_bootstrap_declare` treats a transaction as a "bootstrap" transaction purely based on attacker-controlled fields: [1](#0-0) 

`bootstrap_address()` is simply the felt encoding of the literal string `'BOOTSTRAP'` — a fixed, publicly known constant, not a real deployed account contract. Any user can put this exact value in the `sender_address` field of a `DeclareTransaction`.

The blockifier's execution path checks this flag and, when true, entirely skips `perform_pre_validation_stage` (nonce/fee checks) and the `validate_tx` call (the account's `__validate_declare__`, i.e., the actual signature/authorization check), going straight to declaring the class: [2](#0-1) 

The `charge_fee` flag that `is_bootstrap_declare` depends on is derived from `enforce_fee`, which purely inspects the transaction's self-declared resource bounds/tip: [3](#0-2) [4](#0-3) 

An attacker can trivially produce a zero-fee resource-bounds set (e.g. the same shape as `AllResourceBounds::new_unlimited_gas_no_fee_enforcement`, which sets `l2_gas.max_price_per_unit = GasPrice(0)`), making `enforce_fee` return `false` and thus `charge_fee == false`: [5](#0-4) 

This same `charge_fee` value (computed the same way, from the same attacker-controlled fields) is used identically both at gateway admission time and at real block-execution time: [6](#0-5) 

There is no check preventing this from ever being sent by a normal RPC client. `check_declare_permissions` only enforces an optional allow-list (`authorized_declarer_accounts`); when unset (the default), any sender address — including `'BOOTSTRAP'` — is permitted to submit declare transactions: [7](#0-6) 

The exact same unconditioned bypass exists in the Starknet OS Cairo re-execution code, meaning the state commitment / OS proof will match this behavior rather than diverge from it — the bypass is baked into consensus, not merely a client-side quirk: [8](#0-7) 

In none of these code paths is there a check that this is genuinely the genesis/bootstrap phase of the chain (e.g., block number == 0, or a one-shot "bootstrap done" flag). The mechanism is a permanent, always-reachable code path.

### Impact Explanation
This is directly analogous to the reported Cobbler CWE-732 issue: a privileged/internal-only operation (bootstrapping the system by declaring classes with no account) is gated by an easily-forged "credential" (`sender_address == 'BOOTSTRAP'`) instead of real authentication (a signature validated against a registered account). Any unprivileged transaction sender can:
- Declare arbitrary classes with **no signature check** (an unauthorized account action — normally `__validate_declare__` is the only gate protecting a declare from unauthenticated actors),
- **Bypass fee payment entirely**, undermining the fee mechanism that is supposed to guarantee that every state-changing operation is paid for,
- Repeat this indefinitely for different `class_hash` values (the `prev_value == 0` dict_update only prevents redeclaring the *same* class hash, not reuse of the mechanism itself), since `nonce` never increments for this path.

Because the same unconditioned logic exists in both the Rust blockifier and the Cairo Starknet OS, all honest nodes will agree on accepting these transactions — so this is not a consensus-divergence bug, but rather a systemic authorization bypass that lets anyone perform a state-changing, privileged-looking action without paying for it or proving ownership of any account, at any point after genesis.

### Likelihood Explanation
Trivial to trigger: the attacker only needs to set three fields in a self-crafted `DeclareTransaction` (`sender_address`, `nonce`, and zero-priced resource bounds) — no valid signature, no funded account, and no special network position is required. The `check_declare_permissions` allow-list is opt-in and unset by default, so no additional barrier exists in a default deployment.

### Recommendation
Restrict the bootstrap-declare fast path so it can only be exercised during genuine chain bootstrapping (e.g., gate it on a one-time "genesis not yet finalized" flag or block number == 0, and/or require that this special sender address can never be attacker-supplied through the public gateway). At minimum, `check_declare_permissions` (or an equivalent stateful check) should explicitly reject any externally submitted transaction with `sender_address == bootstrap_address()` outside of the node's internal genesis-provisioning flow, in both the blockifier (`account_transaction.rs`) and the Starknet OS Cairo implementation (`transaction_impls.cairo`).

### Proof of Concept
1. Construct a `DeclareTransactionV3` with:
   - `sender_address = DeclareTransaction::bootstrap_address()` (felt of `'BOOTSTRAP'`)
   - `nonce = Nonce(Felt::ZERO)`
   - `resource_bounds` set to a zero-fee configuration (e.g. `l2_gas.max_price_per_unit = GasPrice(0)`, as in `AllResourceBounds::new_unlimited_gas_no_fee_enforcement`)
   - Arbitrary `class_hash` / `compiled_class_hash` for a class the attacker controls, and an empty/garbage signature.
2. Submit via the public gateway `add_tx` RPC. `check_declare_permissions` passes (no allow-list configured by default). Stateful validation computes `charge_fee = enforce_fee(...) = false`, and `StatefulValidator::perform_validations` calls `self.execute(tx)` for Declare, which hits `is_bootstrap_declare(charge_fee=false) == true` and short-circuits, declaring the class without ever calling `__validate_declare__`.
3. The transaction is admitted to the mempool and, at block-building time, `AccountTransaction::new_for_sequencing` computes the same `charge_fee` value, so `execute_raw` again takes the bootstrap short-circuit, permanently declaring the attacker's class on-chain with zero fee and no signature check.

This is confirmed as reachable/intended-by-design in existing tests such as `test_bootstrap_declare` (`crates/blockifier/src/transaction/account_transactions_test.rs:905-991`), which shows a bootstrap-address declare with default (empty) signature and zero fee succeeding and mutating state — the missing piece is that nothing restricts this path to genesis-only submission.

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

**File:** crates/blockifier/src/transaction/transactions.rs (L379-383)
```rust
/// Determines whether the fee should be enforced for the given transaction.
pub fn enforce_fee(tx: &AccountTransaction, only_query: bool) -> bool {
    // TODO(AvivG): Consider implemetation without 'create_tx_info'.
    tx.create_tx_info(only_query).enforce_fee()
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

**File:** crates/starknet_api/src/transaction/fields.rs (L493-505)
```rust
    pub fn new_unlimited_gas_no_fee_enforcement() -> Self {
        let default_l2_gas_amount = GasAmount(HIGH_GAS_AMOUNT); // Sufficient to avoid out of gas errors.
        let default_resource =
            ResourceBounds { max_amount: GasAmount(0), max_price_per_unit: GasPrice(1) };
        Self {
            l1_gas: default_resource,
            l2_gas: ResourceBounds {
                max_amount: default_l2_gas_amount,
                max_price_per_unit: GasPrice(0), // Set to zero for no enforce_fee mechanism.
            },
            l1_data_gas: default_resource,
        }
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L308-312)
```rust
        let only_query = false;
        let charge_fee = enforce_fee(executable_tx, only_query);
        let strict_nonce_check = false;
        let execution_flags =
            ExecutionFlags { only_query, charge_fee, validate: !skip_validate, strict_nonce_check };
```

**File:** crates/apollo_gateway/src/gateway.rs (L407-433)
```rust
    fn check_declare_permissions(
        &self,
        declare_tx: &RpcDeclareTransaction,
    ) -> Result<(), StarknetError> {
        // TODO(noamsp): Return same error as in Python gateway.
        if self.config.static_config.block_declare {
            return Err(StarknetError {
                code: StarknetErrorCode::UnknownErrorCode(
                    "StarknetErrorCode.BLOCKED_TRANSACTION_TYPE".to_string(),
                ),
                message: "Transaction type is temporarily blocked.".to_string(),
            });
        }
        let RpcDeclareTransaction::V3(declare_v3_tx) = declare_tx;
        if !self.config.is_authorized_declarer(&declare_v3_tx.sender_address) {
            return Err(StarknetError {
                code: StarknetErrorCode::KnownErrorCode(
                    KnownStarknetErrorCode::UnauthorizedDeclare,
                ),
                message: format!(
                    "Account address {} is not allowed to declare contracts.",
                    &declare_v3_tx.sender_address
                ),
            });
        }
        Ok(())
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
