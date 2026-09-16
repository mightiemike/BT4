### Title
Unauthenticated `BOOTSTRAP` declare transactions allow any unprivileged sender to bypass fees, nonce checks, and signature validation - ([File: crates/starknet_api/src/executable_transaction.rs])

### Summary
The "bootstrap declare" fast path — intended only to let the sequencer seed a brand-new chain with its first class declarations — is gated purely by a hard-coded, keyless sender address (`'BOOTSTRAP'`), a literal `nonce == 0`, and `charge_fee == false`. There is no cryptographic or access-control check tying this privileged path to the actual sequencer/genesis process. Any external, unprivileged transaction sender can construct an `RpcTransaction::Declare` with `sender_address = bootstrap_address()`, `nonce = 0`, zero-fee resource bounds, and an empty signature, and the gateway/blockifier will accept and execute it with **no signature validation, no fee, and no nonce/state check**, exactly like a genesis-only op, at any point in the chain's life — not only at genesis.

### Finding Description
`DeclareTransaction::is_bootstrap_declare` treats any V3 declare transaction as the special bootstrap flow purely based on transaction *content*, not sender identity/authentication: [1](#0-0) 

`bootstrap_address()` is just a felt encoding of the string `"BOOTSTRAP"` — not a deployed account, not backed by any key, and not verifiable by signature. The blockifier's `execute_raw` short-circuits the entire account-transaction lifecycle for any transaction that matches this predicate, skipping `perform_pre_validation_stage` (nonce/fee checks), the `__validate_declare__` call, and fee charging altogether: [2](#0-1) 

The Starknet OS (Cairo) side mirrors this: it recognizes `sender_address == 'BOOTSTRAP' and nonce == 0 and version == 3`, then directly writes the class declaration into `contract_class_changes` and skips the rest of the transaction (`%{ SkipTx %}`), again with no signature/permission check: [3](#0-2) 

At the gateway layer, declare permissions are only restricted if an operator explicitly configures `authorized_declarer_accounts`; by default this is `None`, meaning **any** sender address — including the `BOOTSTRAP` address — is accepted: [4](#0-3) [5](#0-4) 

Because the only state guard is `dict_update{...}(key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash)` (i.e., "this exact class hash hasn't been declared before"), the bootstrap path is not a single genesis-only, one-shot mechanism — it remains callable **forever**, for any never-before-declared class hash, by anyone who crafts a transaction with this sender/nonce/fee combination. The mempool's per-account nonce slot limits an attacker to roughly one such free declare per block cycle (per the documented "stuck in mempool, rejected next attempt" behavior), but this still allows repeated, unbounded, unauthorized, fee-free declarations over time.

This mirrors the LSD `rotateNodeRunnerOfSmartWallet` bug class: a function/path meant to be restricted to a privileged actor (the DAO in the LSD case; "the sequencer" bootstrapping a new system here) is instead reachable by anyone because the actual authorization check is missing or implicit, letting an unprivileged party race ahead of (or entirely replace) the intended privileged operation.

### Impact Explanation
An unprivileged L2 transaction sender can:
1. Permanently bypass the declare-transaction fee and resource-accounting mechanism for arbitrary contract classes, undermining the network's ability to charge for declare-related compute/storage (Sierra→CASM compilation, class storage) — a concrete bypass of fee and resource accounting.
2. Front-run a legitimate paying user's pending declare transaction: since Sierra class hashes are deterministic and public once broadcast/known, an attacker can submit a `BOOTSTRAP` declare for the same class hash first (no fee, no validation delay), causing the honest declarer's subsequent `try_declare` to fail with `DeclareTransactionCasmHashMissMatch`/`DeclareTransactionError` (already declared), while the class ends up permanently attributed to the unauthenticated `BOOTSTRAP` identity rather than the real declarer — an unauthorized state mutation analogous to the referenced report's frontrun-and-block pattern.

### Likelihood Explanation
Constructing the malicious transaction requires no private key (no signature is needed since validation is skipped for this path) and no special privilege — only knowledge of the fixed `bootstrap_address()` felt value, which is present in the open-source code itself. The gateway's default configuration (`authorized_declarer_accounts: None`) does not restrict this. The only friction is the mempool's single-pending-tx-per-nonce constraint, which throttles but does not prevent repeated exploitation.

### Recommendation
Restrict the bootstrap declare fast path so it can only be exercised during genuine genesis/bootstrap of a chain (e.g., gate it behind a one-time, sequencer-controlled flag/state entry that is permanently disabled after the chain's first block, rather than relying solely on `sender_address == 'BOOTSTRAP' && nonce == 0 && !charge_fee`), and/or require the gateway to always reject declare transactions from `bootstrap_address()` once the network has left its genesis phase.

### Proof of Concept
1. Craft an `RpcTransaction::Declare` (V3) with `sender_address = DeclareTransaction::bootstrap_address()`, `nonce = Nonce(0)`, `resource_bounds = ValidResourceBounds::create_for_testing_no_fee_enforcement()` (zero max possible fee), and `signature = TransactionSignature::default()` (empty), targeting any legitimate/valuable class hash not yet declared — this mirrors `generate_bootstrap_declare` in [6](#0-5) .
2. Submit it through the gateway; with default config (`authorized_declarer_accounts: None`), `check_declare_permissions` passes and the transaction reaches the mempool/batcher.
3. The blockifier detects `is_bootstrap_declare(charge_fee=false) == true` and executes `try_declare` directly, skipping `perform_pre_validation_stage` and fee charging, declaring the class for free — as demonstrated by the passing `valid` case in the test suite: [7](#0-6) .
4. If a legitimate user was about to pay to declare the same class, their transaction now fails because the class is already declared, effectively stealing/blocking the declaration slot for free.

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

**File:** crates/apollo_gateway/src/gateway.rs (L407-432)
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
```

**File:** crates/apollo_gateway_config/src/config.rs (L60-76)
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

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L945-991)
```rust
fn test_bootstrap_declare(
    block_context: BlockContext,
    #[case] declare_tx: DeclareTransaction,
    #[case] hash_version: HashVersion,
) {
    let class_info = calculate_class_info_for_testing(
        FeatureContract::Empty(CairoVersion::Cairo1(RunnableCairo1::Casm)).get_class(),
    );
    let contract_class = class_info.contract_class();
    let mut executable_declare = ApiExecutableDeclareTransaction {
        tx: declare_tx.clone(),
        tx_hash: TransactionHash::default(),
        class_info,
    };

    // Update compiled_class_hash in V3 declare txs to match the contract class with the given hash
    // version.
    if let DeclareTransaction::V3(tx) = &mut executable_declare.tx {
        if let ContractClass::V1((casm, _)) = &contract_class {
            tx.compiled_class_hash = casm.hash(&hash_version);
        }
    }
    let compiled_class_hash = executable_declare.tx.compiled_class_hash();
    let declare_account_tx = AccountTransaction::new_for_sequencing(
        ApiExecutableTransaction::Declare(executable_declare),
    );

    let mut state = CachedState::from(DictStateReader::default());
    let res = declare_account_tx.execute(&mut state, &block_context).unwrap();

    // Check declaration.
    assert_eq!(
        state.get_compiled_class_hash(declare_tx.class_hash()).unwrap(),
        compiled_class_hash
    );

    // Ensure the only change is the class declaration: no fees, nonce bump, etc.
    assert_eq!(res, TransactionExecutionInfo::default());
    assert_eq!(
        state.to_state_diff().unwrap().state_maps,
        StateMaps {
            compiled_class_hashes: HashMap::from([(declare_tx.class_hash(), compiled_class_hash)]),
            declared_contracts: HashMap::from([(declare_tx.class_hash(), true)]),
            ..Default::default()
        }
    );
}
```
