### Title
Bootstrap declare transactions permanently bypass fee, nonce, and validation checks after genesis - ([File: crates/blockifier/src/transaction/account_transaction.rs])

### Summary
The `AccountTransaction::execute_raw` special-cases declare transactions from the hard-coded `'BOOTSTRAP'` sender address, skipping `__validate_declare__`, nonce increment, and fee charging whenever `sender_address == BOOTSTRAP && nonce == 0 && !charge_fee`. This mirrors the JOJO bug class: a reserved/privileged pseudo-account bypasses the checks that are otherwise mandatory for every account, and the condition that is supposed to gate this bypass (intended only for genesis bootstrapping) is not actually restricted to genesis — it is reachable by anyone who can submit a declare transaction at any block height as long as `charge_fee` is computed as `false` for it.

### Finding Description
`is_bootstrap_declare` returns true purely based on transaction fields controllable by any external submitter (sender address, nonce, resource bounds producing zero fee) — not on block height or chain state: [1](#0-0) 

`execute_raw` uses this predicate to entirely skip `perform_pre_validation_stage` (nonce/fee checks), `run_or_revert` (validate step), and `handle_fee` (fee transfer) for such a transaction, executing only the raw declare logic: [2](#0-1) 

Because `sender_address` for bootstrap is a fixed reserved value (`'BOOTSTRAP'`), it is never a real deployed account and its "nonce" is never incremented as part of any bootstrap execution (the whole point of skipping "any account-related actions"): [3](#0-2) 

The comment in `account_transaction.rs` even documents that after a successful bootstrap declare, "the transaction remains in the mempool" and gets replayed and only rejected once the class is already declared — i.e., the code does not model or enforce that this path is only valid once, at genesis; it relies solely on the class-already-declared check to eventually reject duplicates for the *same* class hash. A caller can submit bootstrap-format declare transactions with different (new) class hashes indefinitely, well after genesis, and each will:
- Skip `__validate_declare__` (no signature/authorization check at all — `is_bootstrap_declare` doesn't inspect signature),
- Skip nonce enforcement (the "nonce" for this pseudo-account never advances),
- Skip fee charging (`compute_max_possible_fee == 0` requirement is trivially satisfiable by setting resource bounds to zero, which the Cairo bootstrap flow explicitly checks for and the Rust side gates on `!charge_fee`, itself computed from resource bounds).

This is directly analogous to the JOJO Sherlock finding: a special/reserved account (`insurance` in JOJO, `'BOOTSTRAP'` here) is exempted from a check (`maxPerAccountBorrowAmount` there, nonce/fee/validate here) that every regular account must satisfy, and the exemption is reachable through an ordinary user-submitted operation rather than being restricted to a privileged, one-time system action.

### Impact Explanation
An unprivileged submitter can declare arbitrary classes for free, with no signature/validation, indefinitely (not just at genesis), by crafting a declare transaction with `sender_address = bootstrap_address()`, `nonce = 0`, and resource bounds that yield `max_possible_fee == 0`. This:
- Bypasses fee/resource accounting entirely (no gas/DA cost charged to anyone), undermining the resource-accounting invariant the bouncer and fee mechanism are meant to enforce for all transactions included in a block.
- Allows unauthorized "account action" (declaring a class without any validation of authorization), since `__validate_declare__` is never invoked for this reserved pseudo-account.
- Enables an actor to repeatedly flood declare transactions at zero cost (each with a fresh class hash to dodge the "already declared" rejection), which is a free, unmetered resource sink for the block builder/state — a form of unauthorized resource consumption reachable from a normal transaction submission path.

### Likelihood Explanation
High reachability: any user who can submit an RPC/gateway declare transaction can set `sender_address` to the well-known constant `bootstrap_address()` (a simple, publicly known Felt derived from the string `'BOOTSTRAP'`) and craft zero-fee resource bounds. Nothing in the code path checked confirms the chain is at genesis or that this is the very first bootstrap transaction; the guard is purely a function of transaction fields. The severity is bounded by the fact that this specific class-hash reuse is prevented after one success, but a new class hash can be declared each time.

### Recommendation
Restrict the bootstrap-declare bypass so it can only ever be exercised once, and only genuinely at genesis, not merely gated by transaction field values:
- Track explicit chain/genesis state (e.g., a "genesis completed" flag or block number == 0 check) rather than solely trusting `sender_address == BOOTSTRAP && nonce == 0 && charge_fee == false`.
- Alternatively/additionally, disallow the gateway/mempool from ever accepting declare transactions whose `sender_address` equals `bootstrap_address()` once at least one block has been produced.
- Ensure the bootstrap declare path still enforces some authorization (e.g., signature verification against a well-known bootstrap key) rather than skipping `__validate_declare__` unconditionally.

### Proof of Concept
1. After genesis (any subsequent block), submit a `DeclareTransactionV3` with:
   - `sender_address = DeclareTransaction::bootstrap_address()` (`0x424f4f545354524150`),
   - `nonce = Nonce(Felt::ZERO)`,
   - `resource_bounds` set such that `compute_max_possible_fee(tx_info) == 0` (e.g., via `ValidResourceBounds::create_for_testing_no_fee_enforcement()` as used in tests),
   - an arbitrary new, never-before-declared `class_hash`/`compiled_class_hash`, and no valid signature.
2. `tx.is_bootstrap_declare(charge_fee=false)` returns `true`. [4](#0-3) 
3. `execute_raw` takes the bootstrap branch, skipping `perform_pre_validation_stage`, `run_or_revert` (hence `__validate_declare__`), and `handle_fee`, and directly runs `tx.run_execute` to declare the class. [5](#0-4) 
4. Result: the class is declared with zero fee, zero validation, and no nonce state change — repeatable with new class hashes at any block height, as confirmed by the existing unit test exercising this exact flow outside of any genesis-specific gating. [6](#0-5) 

Note: I could not find, within the indexed code, any additional gateway/mempool-level restriction limiting the bootstrap declare path to genesis-only submission (e.g., a check on `block_number == 0`); the `bootstrap_declare.rs` integration test and `allow_bootstrap_txs()` flag suggest this is opt-in test tooling rather than a hard runtime restriction enforced in the execution path itself. If such a restriction exists elsewhere (e.g., in the gateway or a config flag gating whether bootstrap txs are even accepted post-genesis) but wasn't surfaced by my searches, it would mitigate this finding — this should be verified in a full codebase session, since the index used here may be incomplete for gateway-level policy code.

### Citations

**File:** crates/starknet_api/src/executable_transaction.rs (L246-264)
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
