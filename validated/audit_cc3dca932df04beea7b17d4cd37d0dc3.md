## Findings Summary

I investigated the Aptos-native analogs to the funding-index path-dependence bug (missing scaling before a value is committed/accepted as authoritative) by searching write-set/TransactionInfo construction, accumulator/Merkle-proof code, and replay/restore-verification paths. The strongest candidate I found is a **self-documented, unpatched gap in `TransactionOutput::ensure_match_transaction_info`**, which is the authoritative check used to confirm that a locally-computed `TransactionOutput` matches a `TransactionInfo` pulled from storage/consensus during replay-verify.

### Title
Replay-Verify Skips Validation of `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` — (File: `types/src/transaction/mod.rs`)

### Finding Description
`ensure_match_transaction_info` validates a computed `TransactionOutput` against a persisted/consensus-certified `TransactionInfo` by checking status, gas, write-set hash (`state_change_hash`), and event root hash [1](#0-0) . It does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, which is explicitly acknowledged in a TODO immediately preceding the `Ok(())` return: [2](#0-1) .

These checkpoint hashes are exactly the fields that bind the periodic state-Merkle-tree root (and, once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, the native-position SMT root) into `TransactionInfoV1`, which is what ultimately gets accumulated into the ledger's transaction accumulator and signed by validators [3](#0-2) . `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is defined as: "execution computes the native-position state root at the checkpoint stage and commits it to `TransactionInfoV1`, so it is consensus-verified" [4](#0-3) .

### Impact Explanation
`ensure_match_transaction_info` is the core sanity check called from the chunk executor and offline replay/debugging tooling (`aptos-debugger`, `cli`) [5](#0-4) . Because it silently skips the checkpoint-hash comparisons, if local execution produces a **different** state-checkpoint root (main state, hot state, or native-position state) than what is stored in the authenticated `TransactionInfo`, replay-verify will still report success. This means a divergence between the executor's actual state-commitment output and the value bound into the (validator-signed) `TransactionInfo`/accumulator would go undetected by this check — i.e., a wrong root could be "accepted as valid" by this integrity gate, defeating its purpose as a replay/consistency guard for state-commitment correctness.

### Likelihood Explanation
This is a real code defect (confirmed by the author's own TODO), but its practical severity today is bounded: `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and `HOT_STATE_ROOT_IN_TXN_INFO` are **not** in `FeatureFlag::default_features()` [6](#0-5) , so they are not currently active on mainnet, and the code comment itself states the gap must be closed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" [2](#0-1) . I could not fully trace whether `state_checkpoint_hash` (the pre-existing, already-mainnet field from `TRANSACTION_INFO_V1`/legacy checkpointing) is validated elsewhere in the executor's own state-checkpoint pipeline (e.g., `DoStateCheckpoint`) as a separate, independent gate outside of this specific function — I found `DoStateCheckpoint::get_state_checkpoint_hashes` which does compare computed vs. "known" checkpoint hashes during normal chunk execution [7](#0-6) , which appears to be the actual commit-time integrity gate. This means `ensure_match_transaction_info`'s gap is most exploitable in **replay-verify/debugger tooling paths** (e.g. `db-tool`'s `replay_on_archive`, explicitly named in the TODO) rather than in the live consensus/commit critical path, which somewhat lowers real-world severity versus a live state-corruption bug.

### Recommendation
Add explicit comparisons of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` between `self` (locally computed) and `txn_info` (expected) inside `ensure_match_transaction_info`, gated appropriately on whether those fields are populated (`Option<HashValue>`), before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` on mainnet — as the existing TODO already instructs.

### Proof of Concept
Not independently demonstrable as a live-mainnet exploit at present because the gating features (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, `HOT_STATE_ROOT_IN_TXN_INFO`) are not yet enabled by default; the gap is best characterized as a pre-existing, developer-acknowledged incomplete-validation defect in `ensure_match_transaction_info` that would allow replay-verify tooling to certify an execution result whose checkpoint state roots silently diverge from the authenticated `TransactionInfo`, once those features are turned on.

Given the explicit feature-flag gating and the uncertainty around whether an independent gate (`DoStateCheckpoint`) already covers the live commit path, I present this as a **medium-confidence** finding rather than a definitively critical, fully-live mainnet vulnerability — I recommend a Devin session with full repo/build access to trace all call sites and confirm whether any commit-path gate independently enforces these checkpoint-hash comparisons before this feature is enabled.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2195)
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
```

**File:** types/src/transaction/mod.rs (L2197-2203)
```rust
        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
```

**File:** types/src/transaction/mod.rs (L2463-2493)
```rust
impl TransactionInfoV1 {
    pub fn new(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        hot_state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
        position_state_checkpoint_hash: Option<HashValue>,
    ) -> Self {
        Self {
            gas_used,
            status,
            transaction_hash,
            event_root_hash,
            state_change_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
            placeholder1: None,
            placeholder2: None,
            placeholder3: None,
            placeholder4: None,
            placeholder5: None,
            placeholder6: None,
            placeholder7: None,
        }
    }
```

**File:** types/src/on_chain_config/aptos_features.rs (L203-206)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
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

**File:** execution/executor/src/chunk_executor/mod.rs (L1-1)
```rust

```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L192-234)
```rust
    fn get_state_checkpoint_hashes(
        execution_output: &ExecutionOutput,
        known_state_checkpoints: Option<Vec<Option<HashValue>>>,
        computed_last_checkpoint_hash: HashValue,
        label: &str,
    ) -> Result<Vec<Option<HashValue>>> {
        let _timer = OTHER_TIMERS.timer_with(&[&format!("get_{label}_checkpoint_hashes")]);

        let num_txns = execution_output.to_commit.len();
        let last_checkpoint_index = execution_output
            .to_commit
            .state_update_refs()
            .last_inner_checkpoint_index();

        if let Some(known) = known_state_checkpoints {
            ensure!(
                known.len() == num_txns,
                "Bad number of known {label} hashes. {} vs {}",
                known.len(),
                num_txns,
            );
            if let Some(idx) = last_checkpoint_index {
                ensure!(
                    known[idx] == Some(computed_last_checkpoint_hash),
                    "{label} root hash mismatch with known hashes passed in. {:?} vs {:?}",
                    known[idx],
                    Some(computed_last_checkpoint_hash),
                );
            }
            Ok(known)
        } else {
            if !execution_output.is_block {
                // We should enter this branch only in test.
                execution_output.to_commit.ensure_at_most_one_checkpoint()?;
            }

            let mut out = vec![None; num_txns];
            if let Some(index) = last_checkpoint_index {
                out[index] = Some(computed_last_checkpoint_hash);
            }
            Ok(out)
        }
    }
```
