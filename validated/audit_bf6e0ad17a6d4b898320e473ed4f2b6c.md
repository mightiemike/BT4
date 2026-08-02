### Title
Replay-verify's `ensure_match_transaction_info` skips checkpoint-hash comparisons, allowing state-root divergence to pass verification - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole invariant-checking function used by replay/backup verification tools (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger`, `aptos-move/cli`) to confirm that a locally re-executed transaction output matches the transaction info that was actually committed and accumulator-authenticated on-chain. It validates status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  compares only status, gas, write-set hash, and event root hash between the locally computed `TransactionOutput` and the historically committed `TransactionInfo`. The function's own comment acknowledges the gap: [2](#0-1) 

This means any field of `TransactionInfoV1` that is carried only in the checkpoint hashes — the sparse-Merkle `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and the repurposed `position_state_checkpoint_hash` (used for the native-trading position state root, see `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in [3](#0-2) ) — is never independently re-verified by this consumer. `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify` calls this exact function as its only correctness gate after re-executing historical transactions: [4](#0-3) . If the locally recomputed state/hot-state/position-state root diverges from the historically committed root (e.g., due to a state-computation bug, a JMT/hot-state bug, or a future bug in position-state root computation once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled), this tool will report a clean, successful replay even though the local ledger state has silently diverged from the authenticated on-chain state.

### Impact Explanation
This breaks the "authenticated API and proof-bearing responses must stay bound to the right ledger version, root, and object" invariant for the replay/restore verification tooling: a state-root divergence — the single most safety-critical class of consensus bug — would go undetected by the primary verification tool designed to catch exactly that class of bug. This matches the required "Hard-fork-only divergence during commit, replay, restore, or proof verification" impact category, since the divergence is only observable/consequential when nodes disagree on the true state root (a hard-fork-class event), and the very tool meant to detect such an event during replay/restore is blind to it.

### Likelihood Explanation
Likelihood is **currently low on mainnet** because `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and `HOT_STATE_ROOT_IN_TXN_INFO` are not present in `FeatureFlag::default_features()` ( [5](#0-4) ), so `position_state_checkpoint_hash`/`hot_state_checkpoint_hash` are not yet populated/consequential on mainnet today. The gap is real and self-acknowledged by the codebase's own TODO, but it is a latent defect that only becomes exploitable/impactful once these features are turned on, or in any deployment/backtest scenario using `HOTNESS_IN_EPILOGUE` (which *is* in `default_features()`) together with hot-state checkpoints where `hot_state_checkpoint_hash` divergence could already occur silently.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in the expected `TransactionInfo`) against locally recomputed values before treating a replayed chunk as verified, and gate `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` enablement on this fix landing, as the in-code comment itself specifies.

### Proof of Concept
Not independently reproducible as an on-chain exploit today because the relevant feature flags are disabled by default; the defect is demonstrated directly by the code path: `execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs:392-405` treats `ensure_match_transaction_info` as a complete correctness check, while that function's implementation in `types/src/transaction/mod.rs:2139-2204` never compares `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, as documented by the function's own TODO comment (lines 2197-2203).

### Citations

**File:** types/src/transaction/mod.rs (L2139-2204)
```rust
    pub fn ensure_match_transaction_info(
        &self,
        version: Version,
        txn_info: &TransactionInfo,
        expected_write_set: Option<&WriteSet>,
        expected_events: Option<&[ContractEvent]>,
    ) -> Result<()> {
        const ERR_MSG: &str = "TransactionOutput does not match TransactionInfo";

        let expected_txn_status: TransactionStatus = txn_info.status().clone().into();
        ensure!(
            self.status() == &expected_txn_status,
            "{}: version:{}, status:{:?}, auxiliary data:{:?}, expected:{:?}",
            ERR_MSG,
            version,
            self.status(),
            self.auxiliary_data(),
            expected_txn_status,
        );

        ensure!(
            self.gas_used() == txn_info.gas_used(),
            "{}: version:{}, gas_used:{:?}, expected:{:?}",
            ERR_MSG,
            version,
            self.gas_used(),
            txn_info.gas_used(),
        );

        let write_set_hash = CryptoHash::hash(self.write_set());
        ensure!(
            write_set_hash == txn_info.state_change_hash(),
            "{}: version:{}, write_set_hash:{:?}, expected:{:?}, write_set: {:?}, expected(if known): {:?}",
            ERR_MSG,
            version,
            write_set_hash,
            txn_info.state_change_hash(),
            self.write_set,
            expected_write_set,
        );

        let event_hashes = self
            .events()
            .iter()
            .map(CryptoHash::hash)
            .collect::<Vec<_>>();
        let event_root_hash = InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash;
        ensure!(
            event_root_hash == txn_info.event_root_hash(),
            "{}: version:{}, event_root_hash:{:?}, expected:{:?}, events: {:?}, expected(if known): {:?}",
            ERR_MSG,
            version,
            event_root_hash,
            txn_info.event_root_hash(),
            self.events(),
            expected_events,
        );

        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
    }
```

**File:** types/src/on_chain_config/aptos_features.rs (L203-209)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
    /// When enabled, execution populates `TransactionInfoV1`'s hot state root hash, so it
    /// is committed to the ledger accumulator. Requires `TRANSACTION_INFO_V1`.
    HOT_STATE_ROOT_IN_TXN_INFO = 123,
```

**File:** types/src/on_chain_config/aptos_features.rs (L230-343)
```rust
impl FeatureFlag {
    pub fn default_features() -> Vec<Self> {
        vec![
            Self::CODE_DEPENDENCY_CHECK,
            Self::TREAT_FRIEND_AS_PRIVATE,
            Self::SHA_512_AND_RIPEMD_160_NATIVES,
            Self::APTOS_STD_CHAIN_ID_NATIVES,
            // Feature flag V6 is used to enable metadata v1 format and needs to stay on, even
            // if we enable a higher version.
            Self::VM_BINARY_FORMAT_V6,
            Self::VM_BINARY_FORMAT_V7,
            Self::MULTI_ED25519_PK_VALIDATE_V2_NATIVES,
            Self::BLAKE2B_256_NATIVE,
            Self::RESOURCE_GROUPS,
            Self::MULTISIG_ACCOUNTS,
            Self::DELEGATION_POOLS,
            Self::CRYPTOGRAPHY_ALGEBRA_NATIVES,
            Self::BLS12_381_STRUCTURES,
            Self::ED25519_PUBKEY_VALIDATE_RETURN_FALSE_WRONG_LENGTH,
            Self::STRUCT_CONSTRUCTORS,
            Self::PERIODICAL_REWARD_RATE_DECREASE,
            Self::PARTIAL_GOVERNANCE_VOTING,
            Self::_SIGNATURE_CHECKER_V2,
            Self::STORAGE_SLOT_METADATA,
            Self::CHARGE_INVARIANT_VIOLATION,
            Self::DELEGATION_POOL_PARTIAL_GOVERNANCE_VOTING,
            Self::APTOS_UNIQUE_IDENTIFIERS,
            Self::GAS_PAYER_ENABLED,
            Self::BULLETPROOFS_NATIVES,
            Self::SIGNER_NATIVE_FORMAT_FIX,
            Self::MODULE_EVENT,
            Self::EMIT_FEE_STATEMENT,
            Self::STORAGE_DELETION_REFUND,
            Self::SIGNATURE_CHECKER_V2_SCRIPT_FIX,
            Self::AGGREGATOR_V2_API,
            Self::SAFER_RESOURCE_GROUPS,
            Self::SAFER_METADATA,
            Self::SINGLE_SENDER_AUTHENTICATOR,
            Self::SPONSORED_AUTOMATIC_ACCOUNT_V1_CREATION,
            Self::FEE_PAYER_ACCOUNT_OPTIONAL,
            Self::AGGREGATOR_V2_DELAYED_FIELDS,
            Self::CONCURRENT_TOKEN_V2,
            Self::LIMIT_MAX_IDENTIFIER_LENGTH,
            Self::OPERATOR_BENEFICIARY_CHANGE,
            Self::BN254_STRUCTURES,
            Self::RESOURCE_GROUPS_SPLIT_IN_VM_CHANGE_SET,
            Self::COMMISSION_CHANGE_DELEGATION_POOL,
            Self::WEBAUTHN_SIGNATURE,
            Self::KEYLESS_ACCOUNTS,
            Self::FEDERATED_KEYLESS,
            Self::KEYLESS_BUT_ZKLESS_ACCOUNTS,
            Self::JWK_CONSENSUS,
            Self::REFUNDABLE_BYTES,
            Self::OBJECT_CODE_DEPLOYMENT,
            Self::MAX_OBJECT_NESTING_CHECK,
            Self::KEYLESS_ACCOUNTS_WITH_PASSKEYS,
            Self::MULTISIG_V2_ENHANCEMENT,
            Self::DELEGATION_POOL_ALLOWLISTING,
            Self::MODULE_EVENT_MIGRATION,
            Self::_REJECT_UNSTABLE_BYTECODE,
            Self::TRANSACTION_CONTEXT_EXTENSION,
            Self::COIN_TO_FUNGIBLE_ASSET_MIGRATION,
            Self::_OBJECT_NATIVE_DERIVED_ADDRESS,
            Self::DISPATCHABLE_FUNGIBLE_ASSET,
            Self::NEW_ACCOUNTS_DEFAULT_TO_FA_APT_STORE,
            Self::OPERATIONS_DEFAULT_TO_FA_APT_STORE,
            Self::CONCURRENT_FUNGIBLE_ASSETS,
            Self::_AGGREGATOR_V2_IS_AT_LEAST_API,
            Self::CONCURRENT_FUNGIBLE_BALANCE,
            Self::_LIMIT_VM_TYPE_SIZE,
            Self::ABORT_IF_MULTISIG_PAYLOAD_MISMATCH,
            Self::_DISALLOW_USER_NATIVES,
            Self::ALLOW_SERIALIZED_SCRIPT_ARGS,
            Self::_USE_COMPATIBILITY_CHECKER_V2,
            Self::ENABLE_ENUM_TYPES,
            Self::_REJECT_UNSTABLE_BYTECODE_FOR_SCRIPT,
            Self::TRANSACTION_SIMULATION_ENHANCEMENT,
            Self::_NATIVE_MEMORY_OPERATIONS,
            Self::_ENABLE_LOADER_V2,
            Self::_DISALLOW_INIT_MODULE_TO_PUBLISH_MODULES,
            Self::COLLECTION_OWNER,
            Self::ENABLE_CALL_TREE_AND_INSTRUCTION_VM_CACHE,
            Self::ACCOUNT_ABSTRACTION,
            Self::BULLETPROOFS_BATCH_NATIVES,
            Self::DERIVABLE_ACCOUNT_ABSTRACTION,
            Self::VM_BINARY_FORMAT_V8,
            Self::ENABLE_FUNCTION_VALUES,
            Self::NEW_ACCOUNTS_DEFAULT_TO_FA_STORE,
            Self::DEFAULT_ACCOUNT_RESOURCE,
            Self::JWK_CONSENSUS_PER_KEY_MODE,
            Self::TRANSACTION_PAYLOAD_V2,
            Self::ORDERLESS_TRANSACTIONS,
            Self::CALCULATE_TRANSACTION_FEE_FOR_DISTRIBUTION,
            Self::DISTRIBUTE_TRANSACTION_FEE,
            Self::ENABLE_LAZY_LOADING,
            Self::MONOTONICALLY_INCREASING_COUNTER,
            Self::_ENABLE_CAPTURE_OPTION,
            Self::ENABLE_TRUSTED_CODE,
            Self::ENABLE_ENUM_OPTION,
            Self::VM_BINARY_FORMAT_V9,
            Self::ENABLE_FRAMEWORK_FOR_OPTION,
            Self::ENABLE_FUNCTION_REFLECTION,
            Self::VM_BINARY_FORMAT_V10,
            Self::SLH_DSA_SHA2_128S_SIGNATURE,
            Self::PUBLIC_STRUCT_ENUM_ARGS,
            Self::MULTISIG_SCRIPT,
            Self::TRANSACTION_LIMITS,
            Self::VERSIONED_TRANSACTION_VALIDATION,
            Self::STORAGE_SLOT_NATIVES,
            Self::ALLOW_FRIEND_ENTRY_VISIBILITY_DOWNGRADE,
            Self::HOTNESS_IN_EPILOGUE,
            Self::ENCRYPTED_TRANSACTIONS,
        ]
    }
```

**File:** storage/db-tool/src/replay_on_archive.rs (L392-405)
```rust
            if let Err(err) = executed_outputs[idx].ensure_match_transaction_info(
                version,
                &expected_txn_infos[idx],
                Some(&expected_writesets[idx]),
                Some(&expected_events[idx]),
            ) {
                cur_txns.drain(0..idx + 1);
                cur_persisted_aux_info.drain(0..idx + 1);
                expected_txn_infos.drain(0..idx + 1);
                expected_events.drain(0..idx + 1);
                expected_writesets.drain(0..idx + 1);

                return Ok(Some(err));
            }
```
