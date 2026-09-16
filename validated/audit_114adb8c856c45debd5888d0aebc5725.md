### Title
Bootstrap declare path allows any unprivileged sender to permanently declare an arbitrary class without signature, fee, or nonce checks - (File: crates/blockifier/src/transaction/account_transaction.rs, crates/starknet_api/src/executable_transaction.rs)

### Summary
Analogous to the Wildcat `onQueueWithdrawal()` bug — a privileged code path that should only be reachable by a trusted caller instead performs its check purely on attacker-controlled data (`msg.sender`/lender address) with no binding to an actual authorization — the sequencer's "bootstrap declare" fast-path in `AccountTransaction::execute_raw` gates a completely unauthenticated, fee-free, validate-free class declaration solely on fields fully controlled by the transaction sender: `sender_address == bootstrap_address()`, `nonce == 0`, and `charge_fee == false`. There is no cryptographic or state-based proof that the caller is actually the trusted genesis/bootstrap operator.

### Finding Description
`DeclareTransaction::is_bootstrap_declare` [1](#0-0)  determines whether a declare transaction should skip `__validate_declare__`, fee charging, and nonce incrementing entirely:

```rust
pub fn is_bootstrap_declare(&self, charge_fee: bool) -> bool {
    if let crate::transaction::DeclareTransaction::V3(tx) = &self.tx {
        return tx.sender_address == Self::bootstrap_address()
            && tx.nonce == Nonce(Felt::ZERO)
            && !charge_fee;
    }
    false
}
```

`bootstrap_address()` is just a hardcoded constant felt (`'BOOTSTRAP'`) [2](#0-1)  — it is not a deployed account, has no public key, and is not gated by any signature check. Anyone constructing a `Declare` V3 RPC transaction can freely set `sender_address` to this constant.

When `is_bootstrap_declare` returns true, `AccountTransaction::execute_raw` takes a special branch that runs `tx.run_execute()` directly and returns, entirely skipping `perform_pre_validation_stage` (nonce/fee pre-checks) and the account `__validate_declare__` call: [3](#0-2) . The Cairo/Starknet-OS equivalent shows the same unguarded condition, gated only on `sender_address == 'BOOTSTRAP' and nonce == 0 and version == 3`, with a fee-based sub-check: [4](#0-3) .

The only other condition, `!charge_fee`, is derived from `enforce_fee(tx, only_query)` which is itself computed from the transaction's own `resource_bounds`/fee_type — again attacker-supplied fields, not an independent authorization signal [5](#0-4) . There is no check anywhere in this code path that the current block is the genesis block, that the chain has not yet been bootstrapped, or that any privileged entity authorized this specific bootstrap declaration.

This is structurally the same root cause as the referenced Wildcat finding: a function meant to be restricted to a trusted caller/context instead validates only caller-supplied fields that are trivially spoofable (`isKnownLenderOnMarket[lender][msg.sender]` in Wildcat vs. `sender_address == bootstrap_address()` here), allowing any unprivileged sender to trigger privileged behavior.

### Impact Explanation
Exploiting this allows any unprivileged transaction sender to permanently declare an arbitrary contract class (bypassing signature validation, fee payment, and nonce enforcement) at any point after genesis, not just during the intended one-time bootstrap phase. Because `dict_update{... prev_value=0 ...}` in the Starknet OS enforces "declare once" semantics [6](#0-5) , an attacker can race to front-run and permanently squat/declare desired class hashes for free before their legitimate owners, causing denial of service for legitimate declare transactions (class-hash squatting) and free (unpaid) use of the network's declare capacity — a resource-accounting and fee-bypass violation, plus an unauthorized state mutation of the class commitment (affecting the state root / block hash), reachable directly from a single unprivileged declare transaction.

### Likelihood Explanation
High: the “bootstrap” status is derived entirely from fields under attacker control (`sender_address`, `nonce`, and resource bounds that drive `charge_fee`). No genesis-only / one-time-use / height-based guard was found gating this logic in `is_bootstrap_declare`, `execute_raw`, or the Cairo OS declare handler within the retrieved code. Any user capable of submitting a standard V3 declare RPC transaction can attempt this exploit, requiring no special privileges, keys, or timing beyond crafting the transaction fields correctly.

### Recommendation
Restrict the bootstrap-declare fast path so it can only execute in a genuinely one-time, height-gated bootstrap context (e.g., only accepted when the committed state is empty/at genesis, tracked by a persistent flag rather than by attacker-supplied `sender_address`/`nonce`/fee fields), or remove the fee/nonce-based gating entirely in favor of an explicit protocol-level flag that cannot be forged by a regular transaction sender. At minimum, add an explicit check in the gateway/mempool and in `AccountTransaction::execute_raw` that rejects any `Declare` transaction with `sender_address == bootstrap_address()` once the chain has left the bootstrap phase (e.g., once any block > genesis has been committed, or once a one-shot "bootstrap completed" flag has been set in state).

### Proof of Concept
1. An unprivileged user crafts a standard RPC `Declare` V3 transaction with:
   - `sender_address = bootstrap_address()` (the public constant `0x424f4f545354524150`, i.e. `'BOOTSTRAP'`),
   - `nonce = 0`,
   - `resource_bounds` chosen such that `enforce_fee(tx, only_query)` evaluates to `false` (e.g. zero max fee / no-fee-enforcement bounds, as used in `AllResourceBounds::new_unlimited_gas_no_fee_enforcement()` in test helpers [7](#0-6) ),
   - an empty/default `signature` (no valid signature is required since `__validate_declare__` is skipped),
   - an arbitrary `class_hash`/`compiled_class_hash` for the class the attacker wants declared (or wants to block from being declared by its rightful owner).
2. The gateway/mempool accepts and forwards the transaction because no code path rejects `sender_address == bootstrap_address()` outside of an actual genesis-only context.
3. During execution, `AccountTransaction::execute_raw` detects `tx.is_bootstrap_declare(charge_fee=false)` is true and takes the fast path [3](#0-2) , calling `run_execute` directly, which in `try_declare` sets the class in state if not already declared [8](#0-7) .
4. The class hash is committed to state with no fee charged and no nonce incremented — confirmed by the existing unit test `test_bootstrap_declare`, which demonstrates exactly this state transition (declared_contracts + compiled_class_hashes updated, no fee/nonce changes) using only `sender_address = bootstrap_address()` [9](#0-8) .
5. Since this test constructs the transaction the same way any external RPC caller could (setting `sender_address` to the public bootstrap constant), the same transaction shape is reachable from an ordinary unprivileged RPC submission at any point in the chain's lifetime — not only genesis — unless a height/one-shot gate exists elsewhere that this analysis could not locate.

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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L899-911)
```rust
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

**File:** crates/central_systest_blobs/src/cende_blob_regression_test.rs (L450-459)
```rust
    /// If the sender address is None, create a bootstrap declare tx.
    /// Otherwise, create a regular declare tx (with fees).
    fn make_declare_tx(&mut self, contract: FeatureContract, sender: Option<ContractAddress>) {
        let (bootstrap_mode, sender_address, resource_bounds, nonce) = match sender {
            None => (
                true,
                ExecutableDeclareTx::bootstrap_address(),
                AllResourceBounds::new_unlimited_gas_no_fee_enforcement(),
                Nonce::default(),
            ),
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
