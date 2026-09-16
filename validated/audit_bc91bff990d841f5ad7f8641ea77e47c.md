### Title
Unauthenticated permanent bootstrap-declare backdoor allows any transaction sender to free-declare classes indefinitely when `authorized_declarer_accounts` is unset (default) - ([File: crates/apollo_gateway_config/src/config.rs], [File: crates/starknet_api/src/executable_transaction.rs], [File: crates/blockifier/src/transaction/account_transaction.rs])

### Summary
The sequencer implements a special "bootstrap declare" transaction path intended to let the operator seed initial classes into a fresh chain without paying fees or running account validation. This mechanism is gated only by a magic, publicly-known sender address (`'BOOTSTRAP'`), a fixed nonce of `0`, and `charge_fee == false`—there is no time-window, block-height, or one-time-use restriction analogous to the nginx-ui advisory's "first-boot" gate, and by default no allowlist restricts who may submit declare transactions from arbitrary addresses.

### Finding Description
The bootstrap sender address is a hardcoded, publicly known constant — the ASCII felt encoding of `"BOOTSTRAP"`: [1](#0-0) 

Any V3 declare transaction with `sender_address == bootstrap_address()`, `nonce == 0`, and no fee charged is treated as a "bootstrap declare": the OS/blockifier skip account validation, nonce increment, and fee collection entirely, and unconditionally record a class declaration: [2](#0-1) [3](#0-2) 

The same special case is mirrored in the Starknet OS Cairo program that re-executes transactions, confirming this is a first-class transaction-execution rule, not merely an operational script: [4](#0-3) 

Crucially, the only gate at the gateway layer that could restrict who is allowed to submit a declare transaction (and thus who can use the bootstrap sender address) is `authorized_declarer_accounts`, which **defaults to `None`, meaning any address — including the bootstrap address — is authorized to declare**: [5](#0-4) [6](#0-5) 

This default (`None`) is also what ships in the production/deployment app-config templates: [7](#0-6) 

Because the bootstrap sender's nonce is never incremented (execution returns `TransactionExecutionInfo::default()` and skips `check_and_increment_nonce`), the same sender address can submit a new bootstrap-declare transaction with `nonce == 0` again and again, forever — the mechanism is not a one-time genesis action; it is a permanently reachable code path with no expiration, analogous to the nginx-ui advisory's install endpoint accepting bootstrap data with no time/authorization boundary. The only per-class protection is `dict_update{...}(prev_value=0, ...)`, which prevents re-declaring the *same* class hash twice, but does not prevent declaring arbitrarily many *different* classes for free from this special sender at any point in the chain's lifetime.

### Impact Explanation
An unprivileged party who can submit RPC transactions to the gateway (any external declarer, since `authorized_declarer_accounts` is `None` by default) can craft V3 declare transactions using `sender_address = bootstrap_address()`, `nonce = 0`, and zero resource bounds (`compute_max_possible_fee == 0`). Each such transaction:
- Skips `__validate_declare__` entirely (no account/signature check is possible since the bootstrap address has no deployed contract),
- Skips fee charging and resource-bound enforcement that normally protects the bouncer/mempool from unbounded compute consumption,
- Declares an arbitrary Sierra/CASM class permanently into state at zero cost.

This breaks the fee-market invariant that all state-changing declare operations be paid for, and provides an unauthenticated, unmetered channel to inject class declarations into every block indefinitely post-genesis — a class of "unauthorized account action" (writing to `contract_class_changes` without paying fees or passing account authentication) with a straightforward DoS/cost-externality vector against the compiler/bouncer resources (since fee accounting and mempool prioritization for this path are bypassed).

### Likelihood Explanation
High. There is no code enforcing that the bootstrap declare path is only usable during genesis or by a designated bootstrapper — the check is purely structural (`sender_address`, `nonce`, `charge_fee`), and `authorized_declarer_accounts` defaults to unrestricted (`None`) both in code defaults and in the shipped deployment configs. Any party capable of submitting a declare transaction to the gateway can trivially construct one with `sender_address = bootstrap_address()`.

### Recommendation
1. Restrict bootstrap-declare eligibility to a specific, time/height-bounded window (e.g., only accepted at genesis / block 0), analogous to removing "reliance on a time window as a security boundary" but implemented as an explicit, enforced chain-state check (e.g., current block number == 0, or a one-time flag consumed after first use) rather than an indefinitely reachable sender-address pattern.
2. Do not rely solely on `authorized_declarer_accounts` defaulting to `None`; explicitly deny the `bootstrap_address()` sender in `is_authorized_declarer`/`check_declare_permissions` once bootstrap has completed, or require it to be defined only in a dedicated bootstrap component, not the general-purpose gateway.
3. Ensure the nonce/one-time-use semantics prevent unlimited reuse of the bootstrap sender for multiple distinct classes beyond the initial genesis declarations (e.g., disable the special-case entirely after the first block is produced).
4. Add regression tests asserting that `is_bootstrap_declare` transactions are rejected by the gateway/mempool for any block height greater than the designated bootstrap window.

### Proof of Concept
Given default configuration (`authorized_declarer_accounts: None`), an attacker submits an RPC V3 declare transaction with:
```
sender_address = 0x424f4f545354524150   // ASCII "BOOTSTRAP"
nonce = 0
resource_bounds = zero (max_possible_fee == 0)
class_hash = <attacker Sierra class hash>
compiled_class_hash = <matching CASM hash>
signature = []
```
This transaction passes `check_declare_permissions` (no allowlist configured), passes stateless/stateful validation (bootstrap path skips fee/resource checks per `validate_resource_bounds` bootstrap comment), and at execution time hits the `is_bootstrap_declare` branch in `account_transaction.rs`/the OS Cairo code, which declares the class for free with no signature or account validation, as demonstrated by the existing test harness: [8](#0-7) [9](#0-8) 

The attacker can repeat this with a new `class_hash`/`compiled_class_hash` in each subsequent transaction (nonce always remains `0`), producing unlimited free class declarations at any point after genesis.

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

**File:** crates/apollo_gateway_config/src/config.rs (L60-75)
```rust
impl Default for GatewayStaticConfig {
    fn default() -> Self {
        Self {
            stateless_tx_validator_config: StatelessTransactionValidatorConfig::default(),
            stateful_tx_validator_config: StatefulTransactionValidatorConfig::default(),
            contract_class_manager_config: ContractClassManagerConfig {
                contract_cache_size: 300,
                ..Default::default()
            },
            chain_info: ChainInfo::default(),
            block_declare: false,
            authorized_declarer_accounts: None,
            max_concurrent_declare_compilations: DEFAULT_MAX_CONCURRENT_DECLARE_COMPILATIONS,
            proof_archive_writer_config: ProofArchiveWriterConfig::default(),
        }
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

**File:** crates/apollo_deployments/resources/app_configs/gateway_config.json (L1-4)
```json
{
  "gateway_config.static_config.authorized_declarer_accounts": "",
  "gateway_config.static_config.authorized_declarer_accounts.#is_none": true,
  "gateway_config.static_config.block_declare": false,
```

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L905-911)
```rust
#[rstest]
#[case::valid(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    class_hash: class_hash!(7_u64),
    compiled_class_hash: CompiledClassHash(8_u64.into()),
    ..Default::default()
}), HashVersion::V2)]
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
