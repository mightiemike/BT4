Based on the evidence gathered, the "bootstrap declare" special-case in the sequencer is a genuine analog to the reported access-control gap, and it is reachable directly from a single submitted transaction (analogous to gateway/mempool/blockifier/OS execution paths explicitly in scope).

### Title
Unauthenticated `BOOTSTRAP`-sender Declare path lets any submitter register an unverified `class_hash → compiled_class_hash` mapping, bypassing signature validation, fee enforcement, and CASM-hash verification - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo])

### Summary
`execute_declare_transaction` contains a special-cased "bootstrap" branch intended only for genesis bootstrapping of a fresh chain. It is gated solely by three attacker-controllable transaction fields — `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `tx_info.version == 3` — plus a zero `max_possible_fee`, with **no cryptographic authentication of the caller**: ` [1](#0-0) `. The mirrored Rust-side gate is `DeclareTransaction::is_bootstrap_declare`, which checks the exact same three conditions (`sender_address`, `nonce == 0`, `!charge_fee`) with no signature/ownership check: ` [2](#0-1) `. When this path is taken, `AccountTransaction::execute_raw` skips `perform_pre_validation_stage` (nonce/fee checks) and the account's `__validate_declare__` entirely, running only the raw declare execution: ` [3](#0-2) `.

### Finding Description
Because `'BOOTSTRAP'` (`crate::executable_transaction::DeclareTransaction::bootstrap_address()`, a hardcoded felt encoding of the ASCII string "BOOTSTRAP") is not a real deployed account with a private key, **anyone** can construct a `DeclareTransaction::V3` with `sender_address = bootstrap_address()`, `nonce = 0`, a default/empty signature, and zero-fee resource bounds. The comment in the test harness even documents that "Bootstrap declare txs are unique: they are sent from a special address and do not increment its nonce," meaning the nonce never advances past 0 and this path can, in principle, be re-invoked repeatedly, not just once at genesis: ` [4](#0-3) `.

Critically, the Cairo-OS bootstrap branch commits `[class_hash_ptr] → compiled_class_hash` via `dict_update` with `prev_value=0` **without verifying** that `compiled_class_hash` is the correct compiled-class hash of the declared Sierra/CASM class — that verification (`check_compile_class_hash_v2_declaration` / CASM-hash-mismatch checks) is only exercised in the normal (non-bootstrap) declare flow, as shown by the accompanying Rust test `test_bootstrap_declare`, where the "wrong compiled_class_hash" case is only caught for tx version mismatches, not for the class-hash pre-image check that the bootstrap path skips: ` [5](#0-4) `. This mirrors the structural weakness in the reported bug: a public, unauthenticated entry point writes an attacker-supplied value into a mapping (`workersPublicKey` / here `class_hash_to_compiled_class_hash`) that other honest participants (or the OS/committer) subsequently trust.

The only defense preventing this from being freely exploitable in a live network is the gateway's generic "no zero resource bounds" stateless check, which would reject the required zero-fee resource bounds for a non-privileged caller: ` [6](#0-5) `. However, this check is a **global config flag** (`validate_resource_bounds` / `allow_bootstrap_txs`) rather than a check bound to a specific bootstrap window (e.g., "only accept while chain has zero blocks"), and it is explicitly disabled by design whenever bootstrap transactions must be allowed: ` [7](#0-6) `. There is no on-chain/state-based gate (e.g., "only if `latest_block_number == None`") enforced at the blockifier/OS execution layer itself — the restriction lives entirely in a deployment-time gateway config toggle, not in the state-dependent logic that actually performs the privileged write.

### Impact Explanation
If a node/chain is deployed with `validate_resource_bounds`/bootstrap-mode left enabled beyond the intended one-time genesis window (a plausible operational/config error given there is no automatic, state-derived cutoff), any unauthenticated transaction sender can:
- Declare arbitrary classes for free, with `class_hash → compiled_class_hash` entries that are **never checked against the actual compiled artifact**, permanently corrupting the declared-classes state (a wrong committed state root for that class-hash mapping), since `prev_value=0` in `dict_update` means only the *first* declaration of any given `class_hash` "wins" and cannot be corrected later.
- Cause honest-node divergence if some nodes still run in a stricter validation mode while others accept the bootstrap path, or if the corrupted mapping is later relied upon by account deployment/declare flows expecting a valid CASM hash for that class.
- This is a config/design-boundary access-control gap directly analogous to the reported vulnerability: a "special path" meant to be privileged (workers-only / genesis-only) is guarded only by data values in the request rather than an enforced authorization/state check.

### Likelihood Explanation
Exploitation requires the deployment to have `allow_bootstrap_txs`/`validate_resource_bounds=false` active (or a misconfiguration leaving it on) at a time when the chain is no longer at genesis. This is a real, plausible operational condition since the flag is a static gateway config rather than a dynamically-derived, block-number-gated invariant — i.e., nothing in the blockifier/OS code itself prevents replaying this transaction post-genesis if the gateway happens to accept zero-bound transactions. I could not fully confirm from the available context whether there is an additional enforcement point (e.g., in the batcher/mempool or a stateful gateway validator) that independently rejects `sender_address == 'BOOTSTRAP'` once the chain has advanced past block 0; this residual protection, if present elsewhere, would reduce likelihood, but was not found in the reviewed code paths.

### Recommendation
- Bind the bootstrap-declare bypass to an explicit, state-derived, one-time condition (e.g., only permitted when `latest_block_number` is `None`/chain height is 0), enforced at the execution layer (both Rust `is_bootstrap_declare` and the Cairo OS `execute_declare_transaction` branch), not merely via a static gateway config flag.
- Require the declared `compiled_class_hash` to still be validated against the actual class artifact even in the bootstrap path, removing the special-cased skip of CASM-hash verification.
- Ensure `allow_bootstrap_txs`/`validate_resource_bounds` cannot remain enabled in a node config after the genesis block has been produced (fail-safe default, or automatic transition).

### Proof of Concept
Not directly executable from static analysis alone; conceptually: submit an `RpcTransaction::Declare` (V3) with `sender_address = DeclareTransaction::bootstrap_address()`, `nonce = 0`, `signature = default`, and `resource_bounds = ValidResourceBounds::create_for_testing_no_fee_enforcement()` (as used in `generate_bootstrap_declare` in `crates/mempool_test_utils/src/starknet_api_test_utils.rs:585-595`) to a node running with bootstrap/zero-resource-bounds validation enabled, choosing an arbitrary `class_hash`/`compiled_class_hash` pair. This is confirmed functional (though intended for legitimate genesis use) by the existing integration test `crates/apollo_integration_tests/tests/bootstrap_declare.rs:1-35` and unit test `crates/blockifier/src/transaction/account_transactions_test.rs:905-991`.

### Citations

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

**File:** crates/apollo_integration_tests/tests/bootstrap_declare.rs (L19-22)
```rust
/// Bootstrap declare txs are unique: they are sent from a special address and do not increment its
/// nonce. As a result, they are not removed from the mempool upon successful execution, and will
/// only be removed after being rejected during a subsequent attempt.
#[tokio::test(flavor = "multi_thread", worker_threads = 3)]
```

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L905-944)
```rust
#[rstest]
#[case::valid(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    class_hash: class_hash!(7_u64),
    compiled_class_hash: CompiledClassHash(8_u64.into()),
    ..Default::default()
}), HashVersion::V2)]
#[should_panic(expected = "DeclareTransactionCasmHashMissMatch")]
#[case::poseidon_declare_tx(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    class_hash: class_hash!(7_u64),
    compiled_class_hash: CompiledClassHash(8_u64.into()),
    ..Default::default()
}), HashVersion::V1)]
#[should_panic(expected = "UninitializedStorageAddress")]
#[case::wrong_tx_version(DeclareTransaction::V2(DeclareTransactionV2 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    ..Default::default()
}), HashVersion::V2)]
#[should_panic(expected = "InvalidNonce")]
#[case::wrong_nonce(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    nonce: Nonce(felt!(1_u64)),
    ..Default::default()
}), HashVersion::V2)]
#[should_panic(expected = "UninitializedStorageAddress")]
#[case::wrong_sender_address(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ContractAddress(PatriciaKey::from(1_u128)),
    ..Default::default()
}), HashVersion::V2)]
#[should_panic(expected = "InsufficientResourceBounds")]
#[case::non_trivial_resource_bounds(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    resource_bounds: ValidResourceBounds::AllResources(AllResourceBounds {
        l1_gas: ResourceBounds::default(),
        l2_gas: ResourceBounds{max_amount: GasAmount(1), max_price_per_unit: GasPrice(1)},
        l1_data_gas: ResourceBounds::default(),
    }),
    ..Default::default()
}), HashVersion::V2)]
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

**File:** crates/apollo_integration_tests/src/utils.rs (L329-349)
```rust
    allow_bootstrap_txs: bool,
    validation_only: bool,
    verify_state_diff_hash: bool,
) -> (SequencerNodeConfig, ConfigPointersMap) {
    let recorder_url = consensus_manager_config.cende_config.recorder_url.clone();
    let fee_token_addresses = chain_info.fee_token_addresses.clone();
    let storage_reader_server_port = available_ports.get_next_port();
    let mut batcher_config = create_batcher_config(
        storage_config.batcher_storage_config,
        chain_info.clone(),
        block_max_capacity_gas,
        storage_reader_server_port,
    );
    let committer_config = ApolloCommitterConfig {
        db_path: storage_config.committer_db_path.clone(),
        verify_state_diff_hash,
        ..Default::default()
    };
    let validate_non_zero_resource_bounds = !allow_bootstrap_txs;
    let gateway_config =
        create_gateway_config(chain_info.clone(), validate_non_zero_resource_bounds);
```
