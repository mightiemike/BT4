### Title
Unrestricted bootstrap-declare bypass allows any unprivileged sender to skip account validation, nonce checks, and fees for `Declare` transactions - ([File: crates/starknet_api/src/executable_transaction.rs])

### Summary
The declare-transaction execution path contains a special "bootstrap" bypass intended to let the sequencer seed an empty chain with its very first class declarations. This bypass is gated only by transaction *field values* that any unprivileged party can set (`sender_address == BOOTSTRAP_ADDRESS`, `nonce == 0`, `charge_fee == false`), not by any actual authorization, genesis/one-time restriction, or check that the caller is the trusted bootstrapper. Any account can submit a normal `Declare` transaction through the gateway with these crafted fields and get a class declared into state without running `__validate_declare__`, without paying any fee, and without incrementing a nonce — at any point in the chain's life, not just genesis.

### Finding Description
`ApiExecutableTransaction::DeclareTransaction::is_bootstrap_declare` decides whether a declare transaction should skip all account-related logic: [1](#0-0) 

The only checks are `sender_address == bootstrap_address()` (a hardcoded constant derived from the string `'BOOTSTRAP'`), `nonce == 0`, and `!charge_fee`. `sender_address` and `nonce` are transaction fields fully controlled by the submitter, and `charge_fee` is derived from the transaction's own resource bounds via `enforce_fee`, which is itself attacker-controlled (e.g. `ValidResourceBounds::create_for_testing_no_fee_enforcement()` style zero-fee bounds), as used by the transaction builder that prepares txs for sequencing: [2](#0-1) 

When `is_bootstrap_declare` returns true, `AccountTransaction::execute_raw` completely skips `perform_pre_validation_stage` (fee/nonce checks) and the `__validate_declare__` call, directly writing the class hash into `contract_class_changes` and returning a default `TransactionExecutionInfo` (no fee charged, no `CallInfo`): [3](#0-2) 

The same unrestricted logic exists in the Starknet OS Cairo implementation used for re-execution, so an honest node re-executing the block reaches the identical bypass with the same lack of restriction: [4](#0-3) 

At the gateway, the only declare-time gating is `check_declare_permissions`, which enforces `block_declare` and an optional `authorized_declarer_accounts` allowlist. By default `authorized_declarer_accounts` is `None`, meaning "any declarer is authorized" (`is_authorized_declarer` returns `true` when the list is `None`): [5](#0-4) [6](#0-5) 

Nothing in this default-configuration path prevents an arbitrary sender from crafting `sender_address = BOOTSTRAP_ADDRESS`, `nonce = 0`, and zero-fee resource bounds. The only remaining protection is `prev_value = 0` in the `dict_update` when declaring the class hash, which just prevents *re-declaring the same class hash*, not prevents an unauthorized actor from performing a bootstrap declare at all. There is no check tying this bypass to genesis (empty state / block 0) or to a privileged caller — it is reachable identically whether the chain has one block or one million blocks, as long as `authorized_declarer_accounts` is unset (the shipped default in `crates/apollo_deployments/resources/app_configs/gateway_config.json`).

### Impact Explanation
Any unprivileged transaction sender can trigger this comment-documented "sequencer bootstrap" fast path at will by simply setting `sender_address` to the hardcoded `BOOTSTRAP_ADDRESS`, which is a compile-time-known constant with no associated real account, key, or nonce state. This is an unauthorized privileged action: it bypasses account signature validation (`__validate_declare__` is never run — no valid signature is ever required), bypasses fee enforcement entirely, and bypasses the normal nonce-increment/anti-replay bookkeeping, all without any owner/permission check. This lets attackers permanently declare arbitrary Sierra classes for free (once per never-before-declared class hash), consuming Sierra-to-CASM compilation resources and class-declaration state changes without paying for them, and it also produces honest-node divergence risk if any node applies additional restrictions (e.g. only allowing it pre-genesis) while the on-chain logic in blockifier/OS enforces none — the current code accepts these transactions unconditionally at any block height.

### Likelihood Explanation
High. The trigger only requires knowledge of a hardcoded constant (`sender_address = 0x424f4f545354524150`), setting `nonce = 0`, and using resource bounds that make `enforce_fee` return `false` — a pattern already exercised by the codebase's own bootstrap-declare test helpers (`generate_bootstrap_declare`, `create_bootstrap_declare_scenario`). No compromised keys, insider access, or special node privileges are required; the default gateway configuration ships with `authorized_declarer_accounts` unset, i.e., open to all senders.

### Recommendation
Restrict the bootstrap-declare bypass so that it can only be exercised during genesis/bootstrap of the chain — e.g., gate `is_bootstrap_declare` (and its Cairo OS counterpart) on `block_number == 0` (or an equivalent "state is still empty" condition) in addition to the existing field checks, and/or require the gateway to explicitly reject any declare with `sender_address == BOOTSTRAP_ADDRESS` once the chain has progressed past genesis. Consider additionally requiring `authorized_declarer_accounts`/an explicit allowlist for the bootstrap sender regardless of the general declare-authorization config.

### Proof of Concept
1. Attacker crafts an RPC `Declare` (V3) transaction with:
   - `sender_address = ApiExecutableDeclareTransaction::bootstrap_address()` (`0x424f4f545354524150`)
   - `nonce = 0`
   - `resource_bounds` set so `enforce_fee` evaluates to `false` (e.g., `ValidResourceBounds::create_for_testing_no_fee_enforcement()`), matching the pattern in `generate_bootstrap_declare` (`crates/mempool_test_utils/src/starknet_api_test_utils.rs:585-595`).
   - Any class hash / compiled class hash for a class not yet declared.
   - No valid signature is needed since `__validate_declare__` is never invoked.
2. Submits the transaction through the gateway; `check_declare_permissions` passes because `authorized_declarer_accounts` defaults to `None` (allow-all).
3. On execution (block building via blockifier, and later Starknet OS re-execution), `is_bootstrap_declare` returns `true`, so `perform_pre_validation_stage` and `__validate_declare__` are skipped and the class hash is declared into state for free with no nonce increment (`crates/blockifier/src/transaction/account_transaction.rs:899-911`, `crates/apollo_starknet_os_program/.../transaction_impls.cairo:761-776`).
4. Result: an arbitrary, unauthenticated account has declared a class on-chain without paying any fee or passing account validation, at an arbitrary block height long after genesis.

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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L147-155)
```rust
    pub fn new_for_sequencing(tx: Transaction) -> Self {
        let execution_flags = ExecutionFlags {
            only_query: false,
            charge_fee: enforce_fee(&tx, false),
            validate: true,
            strict_nonce_check: true,
        };
        AccountTransaction { tx, execution_flags }
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

**File:** crates/apollo_gateway_config/src/config.rs (L140-146)
```rust
impl GatewayConfig {
    pub fn is_authorized_declarer(&self, declarer_address: &ContractAddress) -> bool {
        match &self.static_config.authorized_declarer_accounts {
            Some(allowed_accounts) => allowed_accounts.contains(declarer_address),
            None => true,
        }
    }
```
