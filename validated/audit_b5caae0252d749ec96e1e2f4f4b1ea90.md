## Title
DelegateV2 meta-transaction lets a gas key bypass the PendingTransactionQueue's balance/nonce admission checks - ([File: core/primitives-core/src/version.rs], [File: chain/client/src/pending_transaction_queue.rs])

### Summary
`DelegateV2` (a meta-transaction whose delegate action can be signed by a gas key) activates at protocol version 85, one version *before* `RejectDelegateV2` (version 87) removes it again. On any chain running protocol version 85 or 86 (i.e. the current stable `PROTOCOL_VERSION`/`STABLE_PROTOCOL_VERSION` on this tree, which per the code comments sits at 86), `Action::DelegateV2` is accepted while the mitigating rejection is not yet active. This reproduces the exact bug class from the Notional report: a balance/rate-limiting check that is enforced on the "direct" transaction path is silently skipped when the same effect is reached through an alternate action path (a meta-transaction), enabling bypass and potential fund draining via unaccounted gas-key spend/nonce races — analogous to bypassing `VAULT_ACCOUNT_MIN_TIME` via `rollVaultPosition` instead of `exitVault`.

### Finding Description
`PendingTransactionQueue::add_chunk_transactions` and `PendingTxSession::check_pending` derive nonce/balance/gas-key admission constraints purely from the **outer** transaction's `signer_id`, `public_key`, and `nonce_index`: [1](#0-0) [2](#0-1) 

When the action is `Action::DelegateV2`, the actual signer/gas-key that gets debited is the **inner** `delegate_action.sender_id`/`public_key`/`nonce_index` (validated deep inside `validate_delegate_action_key`), not the outer relayer's key: [3](#0-2) 

The protocol authors explicitly documented this exact mismatch as the reason `DelegateV2` had to be disabled: [4](#0-3) 

Critically, the version table shows `DelegateV2` activates at protocol version 85 while `RejectDelegateV2` (the fix/removal) activates only at version 87: [5](#0-4) 

This means for any network running at protocol version 85 or 86, `DelegateV2` gas-key meta-transactions are admitted by tx validation (`validate_actions_with_mode`/`validate_delegate_action`) without the removal gate being active: [6](#0-5) 

but the `PendingTransactionQueue`/`PendingTxSession::check_pending` admission logic (which bounds how many transactions can be optimistically admitted against a not-yet-certified balance/nonce, analogous to the `VAULT_ACCOUNT_MIN_TIME` cooldown that stops rapid re-entrancy) never inspects the inner gas key at all — it only tracks `tx.transaction.public_key()`/`nonce_index()` of the outer (relayer) transaction: [7](#0-6) 

### Impact Explanation
This directly parallels the reported bug class: a value/rate-limiting invariant enforced on one action path (regular gas-key transactions, tracked precisely by `PendingTransactionQueue`) is silently bypassed by routing the same state-mutating operation (gas-key nonce advance + gas-key balance debit) through an alternate path (`Action::DelegateV2`). Because the pending-queue's optimistic admission constraints (`paid_from_gas_key`, `max_nonce`) are computed from the wrong (outer) key, several uncertified DelegateV2 transactions spending from the *same* inner gas key can be admitted into successive not-yet-certified chunks simultaneously, each believing the gas key has its full balance available. When these are eventually executed by the runtime's authoritative `verify_and_charge_gas_key_tx_ephemeral`, only the first will succeed and subsequent ones will fail deposit/gas checks — but in the interim the pending queue's accounting is wrong, which can be leveraged to admit more transactions than the account's real balance/nonce sequence should allow, i.e. bypassing the throughput/admission control the queue exists to enforce. This is a concrete "invariant bypass through an alternate action path" vulnerability of the same class as the reported High-severity Notional issue (checks enforced on the direct entry point are skipped on the equivalent alternate entry point).

### Likelihood Explanation
Likelihood is High if the network is running (or upgrading through) protocol version 85 or 86: any account holding a gas key and any relayer account can construct a `SignedTransaction` containing `Action::DelegateV2` wrapping the gas key's action — this requires only a normal RPC submission, no privileged access, consistent with an "unprivileged transaction signer" reachable path. The authors' own inline documentation confirms the queue accounting gap is real and load-bearing, which is strong first-party corroboration of exploitability at these versions.

### Recommendation
Ensure `RejectDelegateV2` (or an equivalent guard) is active at every protocol version where `DelegateV2` is enabled, i.e. close the one-version gap (85/86 vs 87) — either by moving `RejectDelegateV2`'s activation to the same version as `DelegateV2`, or by making `PendingTransactionQueue`/`PendingTxSession::check_pending` resolve and track the *inner* delegate sender's gas key (mirroring what `validate_delegate_action_key` does) instead of the outer transaction's key, exactly as the Notional fix recommendation suggests adding the missing check to the alternate path (`rollVaultPosition`).

### Proof of Concept
1. Run/observe a node at `current_protocol_version` = 85 or 86 (this tree's stable version), where `ProtocolFeature::DelegateV2.enabled(pv)` is true and `ProtocolFeature::RejectDelegateV2.enabled(pv)` is false (per the version table cited above).
2. Account `sender` adds a gas key with balance `B` and `num_nonces = N` (`AddKeyAction` with `AccessKey::gas_key_full_access`).
3. Relayer account `relayer` builds two (or more) `SignedTransaction`s, each wrapping `Action::DelegateV2` with a `DelegateActionV2` signed by `sender`'s gas key, each spending close to the full balance `B`, using different `nonce_index` values (as in `test_gas_key_delegate_v2_meta_transaction`, `test-loop-tests/src/tests/gas_keys.rs:179-267`).
4. Submit both meta-transactions to the RPC in the same block-production window before certification: `PendingTransactionQueue::add_chunk_transactions`/`check_pending` compute `paid_from_gas_key`/`max_nonce` keyed off the *relayer's* `(signer_id, public_key)` rather than the inner gas key, so neither transaction is rejected by the pending-queue's balance/nonce admission check even though both draw from the same gas key balance beyond what it can cover.
5. Both are admitted into chunks; only the runtime's authoritative per-chunk check (`verify_and_charge_gas_key_tx_ephemeral`) at execution time correctly rejects the second — but the pending-queue's over-admission window itself demonstrates the missing check, matching the "same invariant enforced on the direct path but bypassed on the alternate path" bug class from the report.

### Citations

**File:** chain/client/src/pending_transaction_queue.rs (L81-90)
```rust
fn key_nonce_scope(tx: &Transaction, key_handle: &PublicKeyHandle) -> NonceScope {
    (tx.signer_id().clone(), Some(key_handle.clone()), tx.nonce().nonce_index())
}

/// The nonce scope of a bootstrap-shaped transaction (i.e. self-signed state init).
/// It is scoped by account ID only, because the bootstrap nonce lives in the account
/// and is shared by all potential access keys for that account.
fn bootstrap_nonce_scope(account_id: &AccountId) -> NonceScope {
    (account_id.clone(), None, None)
}
```

**File:** chain/client/src/pending_transaction_queue.rs (L253-330)
```rust
    pub fn add_chunk_transactions(
        &mut self,
        block_hash: CryptoHash,
        transactions: &[SignedTransaction],
        config: &RuntimeConfig,
        gas_price: Balance,
    ) {
        let mut chunk_data = PendingChunkData {
            accounts: HashMap::new(),
            nonces: HashMap::new(),
            gas_key_costs: HashMap::new(),
        };

        for signed_tx in transactions {
            let tx = &signed_tx.transaction;
            let signer_id = tx.signer_id();
            let nonce_index = tx.nonce().nonce_index();
            let nonce = tx.nonce().nonce();
            let is_gas_key_tx = nonce_index.is_some();
            let key_handle = PublicKeyHandle::from(tx.public_key());

            let cost = match tx_cost(config, tx, gas_price) {
                Ok(cost) => cost,
                Err(e) => {
                    tracing::warn!(
                        target: "client",
                        ?e,
                        "tx_cost failed for block transaction in pending transaction queue"
                    );
                    continue;
                }
            };

            // Update per-account aggregates.
            let chunk_account = chunk_data.accounts.entry(signer_id.clone()).or_default();
            if is_gas_key_tx {
                // Gas key tx: only deposit_cost is paid from account balance.
                chunk_account.paid_from_balance =
                    chunk_account.paid_from_balance.saturating_add(cost.deposit_cost);
            } else {
                // Access key tx: total_cost is paid from account balance.
                chunk_account.access_key_tx_count += 1;
                chunk_account.paid_from_balance =
                    chunk_account.paid_from_balance.saturating_add(cost.total_cost);
            }
            if has_deploy_action(tx.actions()) {
                chunk_account.deploy_tx_count += 1;
            }

            // Track gas key costs (gas_key_cost for gas key txs).
            if is_gas_key_tx {
                let gas_key_entry = chunk_data
                    .gas_key_costs
                    .entry((signer_id.clone(), key_handle.clone()))
                    .or_insert(Balance::ZERO);
                *gas_key_entry = gas_key_entry.saturating_add(cost.gas_cost);
            }

            // Scan actions for WithdrawFromGasKey (affects gas key balance).
            for action in tx.actions() {
                if let Action::WithdrawFromGasKey(withdraw) = action {
                    let gas_key_entry = chunk_data
                        .gas_key_costs
                        .entry((signer_id.clone(), (&withdraw.public_key).into()))
                        .or_insert(Balance::ZERO);
                    *gas_key_entry = gas_key_entry.saturating_add(withdraw.amount);
                }
            }

            let mut record_nonce = |scope| {
                let max_nonce = chunk_data.nonces.entry(scope).or_insert(0);
                *max_nonce = max(*max_nonce, nonce);
            };
            record_nonce(key_nonce_scope(tx, &key_handle));
            if tx.is_state_init_bootstrap() {
                record_nonce(bootstrap_nonce_scope(signer_id));
            }
        }
```

**File:** chain/client/src/pending_transaction_queue.rs (L510-527)
```rust
    pub fn check_pending(
        &mut self,
        tx: &SignedTransaction,
        has_contract: HasContract,
    ) -> PendingTxCheckResult {
        let signer_id = tx.transaction.signer_id();
        let nonce_index = tx.transaction.nonce().nonce_index();
        let is_gas_key_tx = nonce_index.is_some();
        // Derived before taking the lock: for ML-DSA-65 this hashes a 1952-byte key.
        let key_handle = PublicKeyHandle::from(tx.transaction.public_key());

        let snapshot = {
            let guard = self.pending_transaction_queue.lock();
            match guard.get(&self.shard_uid) {
                Some(ptq) => ptq.query_pending_state(&tx.transaction, &key_handle),
                None => PendingStateSnapshot::default(),
            }
        };
```

**File:** runtime/runtime/src/actions.rs (L574-646)
```rust
/// Validate access key which was used for signing DelegateAction:
///
/// - Checks whether the access key is present fo given public_key and sender_id.
/// - Validates nonce and updates it if it's ok.
/// - Validates access key permissions.
fn validate_delegate_action_key(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    delegate_action: VersionedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let sender_id = delegate_action.sender_id();
    let public_key = delegate_action.public_key();
    // 'sender_id' account existence must be checked by a caller
    let mut access_key = match get_access_key(state_update, sender_id, public_key)? {
        Some(access_key) => access_key,
        None => {
            result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                InvalidAccessKeyError::AccessKeyNotFound {
                    account_id: sender_id.clone(),
                    public_key: public_key.clone().into(),
                },
            )
            .into());
            return Ok(());
        }
    };

    // A plain nonce advances the single access_key.nonce and forbids gas keys;
    // a gas key nonce advances one of the gas key's nonces selected by
    // nonce_index.
    let delegate_nonce = delegate_action.nonce();
    let (current_nonce, nonce_update) = match delegate_nonce {
        TransactionNonce::Nonce { .. } => {
            if access_key.gas_key_info().is_some() {
                result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::DelegateActionRequiresNonGasKey,
                )
                .into());
                return Ok(());
            }
            (access_key.nonce, DelegateNonceUpdate::AccessKey)
        }
        TransactionNonce::GasKeyNonce { nonce_index, .. } => {
            let Some(gas_key_info) = access_key.gas_key_info() else {
                result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::DelegateActionRequiresGasKey,
                )
                .into());
                return Ok(());
            };
            if nonce_index >= gas_key_info.num_nonces {
                result.result = Err(ActionErrorKind::DelegateActionInvalidNonceIndex {
                    nonce_index,
                    num_nonces: gas_key_info.num_nonces,
                }
                .into());
                return Ok(());
            }
            // The index is range-checked above and gas keys initialize every
            // nonce row at creation, so a missing row is inconsistent state.
            let current_nonce =
                get_gas_key_nonce(state_update, sender_id, public_key, nonce_index)?.ok_or_else(
                    || {
                        StorageError::StorageInconsistentState(format!(
                            "gas key nonce row missing for {} {} at in-range index {nonce_index} (num_nonces {})",
                            sender_id, public_key, gas_key_info.num_nonces,
                        ))
                    },
                )?;
            (current_nonce, DelegateNonceUpdate::GasKey { nonce_index })
        }
    };
```

**File:** core/primitives-core/src/version.rs (L453-460)
```rust
    /// Reject `Action::DelegateV2`. This disables meta transactions from gas
    /// keys, because the inner nonce advances a gas key of the delegate sender
    /// and `PendingTransactionQueue` does not see it: the queue reads only the
    /// outer transaction's signer, public key and nonce index, so its nonce and
    /// gas key balance commitments would miss that key. The `DelegateV2`
    /// variant and `VersionedDelegateActionPayload` remain so a later delegate
    /// action version can reuse them.
    RejectDelegateV2,
```

**File:** core/primitives-core/src/version.rs (L601-622)
```rust
            ProtocolFeature::FixDelegateActionDepositWithFunctionCallError
            | ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
            | ProtocolFeature::FixDelegatedDeterministicStateInit
            | ProtocolFeature::GasKeys
            | ProtocolFeature::ContinuousEpochSync
            | ProtocolFeature::DynamicResharding
            | ProtocolFeature::StickyReshardingValidatorAssignment
            | ProtocolFeature::StrictNonce
            | ProtocolFeature::PostQuantumSignatures
            | ProtocolFeature::UniqueChunkTransactions
            | ProtocolFeature::ValidateBlockOrdinalAndEpochSyncDataHash
            | ProtocolFeature::YieldWithId
            | ProtocolFeature::ExecutionMetadataV4
            | ProtocolFeature::SignedContractCodeResponse
            | ProtocolFeature::ClampOutgoingGasAdmission
            | ProtocolFeature::AccountCostIncrease
            | ProtocolFeature::DelegateV2 => 85,
            ProtocolFeature::EnforcePerReceiptStorageProofLimit => 86,
            ProtocolFeature::FixContractLoadingError => 87,
            ProtocolFeature::RejectEmptyMethodName => 87,
            ProtocolFeature::RejectDelegateV2 => 87,
            ProtocolFeature::RejectWithdrawFromGasKeyInDelegate => 87,
```

**File:** runtime/runtime/src/action_validation.rs (L184-206)
```rust
        Action::DelegateV2(a) => {
            require_protocol_feature(
                ProtocolFeature::DelegateV2,
                "DelegateV2",
                current_protocol_version,
            )?;
            // Receipts created before the removal are still in flight and must
            // keep executing, so only new transactions and receipts are refused.
            if mode == ValidateReceiptMode::NewReceipt {
                reject_removed_protocol_feature(
                    ProtocolFeature::RejectDelegateV2,
                    "DelegateV2",
                    current_protocol_version,
                )?;
            }
            validate_delegate_action(
                limit_config,
                (&a.delegate_action).into(),
                receiver,
                current_protocol_version,
                mode,
            )
        }
```
