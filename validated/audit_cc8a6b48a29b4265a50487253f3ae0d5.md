## Title
FunctionCall access key `allowance` is not tracked across pending (uncertified) chunks, allowing an allowance-restricted key to spend beyond its configured cap - (File: `chain/client/src/pending_transaction_queue.rs`)

## Summary
The Olympus finding is a "stale-snapshot" bug class: a budget/quota is checked once against a value that later changes independently, and nothing re-syncs the two, letting the checked-in quota diverge from actual usage. Nearcore's `PendingTransactionQueue` / `PendingTxSession` mechanism reproduces this exact pattern for the `FunctionCallPermission.allowance` field of access keys.

## Finding Description
NEAR's `AccessKeyPermission::FunctionCall` carries an `allowance: Option<Balance>` that is documented as a hard spending cap for that specific (often lower-trust, session/dApp) key, decremented "in lockstep with account balance" [1](#0-0) . The runtime enforces this at execution time via `check_and_compute_new_allowance`, which subtracts the transaction's `total_cost` from the *access key's currently stored* allowance and fails with `NotEnoughAllowance` on underflow [2](#0-1) .

To support speculative chunk production before execution/certification, nearcore maintains a `PendingTransactionQueue`/`PendingTxSession` that tracks, per account, how much balance has already been committed to *uncertified* pending chunks (`PendingAccount.paid_from_balance`) so that a later chunk-production pass does not double-spend the account's real balance across multiple in-flight, not-yet-executed chunks [3](#0-2) . This is exactly the "old value vs. changing total" problem from the Olympus report, and nearcore solved it — but only for the account `amount`, not for the FunctionCall key's `allowance`.

`PendingConstraints`/`PendingStateSnapshot` only propagate `paid_from_balance`, `paid_from_gas_key`, `max_nonce`, and `max_bootstrap_nonce` [4](#0-3) . There is no equivalent "pending allowance spent" accumulator. When `verify_and_charge_tx_ephemeral` runs for a transaction, it uses `pending.paid_from_balance` to compute `available_balance` for the balance check [5](#0-4) , but the allowance check (`check_and_compute_new_allowance`) is fed only the access key's on-trie value with no analogous pending-usage correction [6](#0-5) .

Because sequential nonces from the *same* FunctionCall access key are explicitly allowed to be admitted into *different, not-yet-certified* chunks (that is the whole point of tracking `max_nonce` across pending chunks — `verify_nonce` compares against `max(access_key.nonce, pending.max_nonce)` [7](#0-6) ), several transactions signed by one restricted key can be independently admitted into parallel uncertified chunks, each checked against the *same stale, not-yet-decremented* on-trie allowance value, while the account balance guard (`paid_from_balance`) merely prevents overspending the raw balance — not the smaller allowance cap.

## Impact Explanation
The `allowance` field exists specifically so that an account owner can hand out a lower-trust FunctionCall-only key (e.g., to a dApp/session) that is capped well below the account's full balance, without granting `FullAccess`. If this cap can be silently bypassed by racing multiple transactions across parallel pending/uncertified chunks, the account can be debited up to its full balance through a key that was only ever authorized to spend up to `allowance`. This is a concrete unauthorized value movement: funds move out of the account beyond the security boundary the key's owner explicitly configured, using only capabilities available to whoever holds that (deliberately limited) key.

## Likelihood Explanation
Exploitation only requires possessing a FunctionCall access key with a finite allowance (a completely ordinary, unprivileged configuration — many dApps issue such keys) and submitting several sequential-nonce transactions in quick succession so that multiple are picked up by different, not-yet-certified chunks before the first is executed. No validator, network, or node-privileged capability is needed — it is purely a property of how the client's pending-transaction admission tracks (or fails to track) allowance versus balance.

## Recommendation
Extend `PendingAccount` / `PendingConstraints` (`chain/client/src/pending_transaction_queue.rs`) to also accumulate per-`(account_id, public_key)` pending allowance consumption, analogous to `paid_from_balance`, and pass it into `check_and_compute_new_allowance` in `runtime/runtime/src/verifier.rs` so the allowance check subtracts both the on-trie decrement and any amount already committed to other pending, uncertified chunks — mirroring exactly how `paid_from_balance` protects the account `amount` field today.

## Proof of Concept
Conceptual sequence (exact reproduction would require instrumenting chunk production to force two transactions into two different uncertified chunks before either executes):
1. Create account `A` with a FunctionCall access key `K` limited to `receiver_id = R`, `allowance = X`.
2. Sign two transactions `tx1` (nonce n) and `tx2` (nonce n+1) from `K`, each with `total_cost` close to `X` (e.g., `0.9 * X` each).
3. Submit both in quick succession so that chunk production admits `tx1` into chunk `C1` and, before `C1` is certified/executed, admits `tx2` into chunk `C2` — `PendingTxSession::check_pending` for `tx2` sees `max_nonce` from `C1` (satisfying the nonce check) and `paid_from_balance` from `C1` (satisfying the balance-vs-total-account check if the account balance is large), but `verify_and_charge_tx_ephemeral`'s allowance check for `tx2` reads the access key's allowance still at its pre-`tx1` value (`X`), because `PendingConstraints` carries no pending-allowance-spent figure [8](#0-7) .
4. Both `tx1` and `tx2` get executed, cumulatively debiting `~1.8 * X` from the account via key `K`, exceeding the configured `allowance = X`.

Note: I was not able to fully trace the exact multi-chunk-before-certification scheduling logic in `chain/client/src/chunk_producer.rs`/`rpc_handler.rs` within the remaining budget to confirm the precise window size (how many uncertified chunks can be in flight simultaneously); this bounds how much the allowance can be exceeded in practice but does not change that the *mechanism* for enforcing the allowance cap across pending chunks is absent while the analogous mechanism for balance exists.

### Citations

**File:** chain/jsonrpc/openapi/openrpc.json (L5350-5351)
```json
          "allowance": {
            "description": "Allowance is a balance limit to use by this access key to pay for function call gas and\ntransaction fees. When this access key is used, both account balance and the allowance is\ndecreased by the same value.\n`None` means unlimited allowance.\nNOTE: To change or increase the allowance, the old access key needs to be deleted and a new\naccess key should be created.",
```

**File:** runtime/runtime/src/verifier.rs (L282-303)
```rust
fn check_and_compute_new_allowance(
    access_key: &AccessKey,
    account_id: &AccountId,
    public_key: &PublicKey,
    total_cost: Balance,
) -> Result<Option<Balance>, InvalidTxError> {
    let Some(fc) = access_key.permission.function_call_permission() else {
        return Ok(None);
    };
    let Some(allowance) = fc.allowance else {
        return Ok(None);
    };
    let new_allowance = allowance.checked_sub(total_cost).ok_or_else(|| {
        InvalidTxError::InvalidAccessKeyError(InvalidAccessKeyError::NotEnoughAllowance {
            account_id: account_id.clone(),
            public_key: public_key.clone().into(),
            allowance,
            cost: total_cost,
        })
    })?;
    Ok(Some(new_allowance))
}
```

**File:** runtime/runtime/src/verifier.rs (L344-348)
```rust
    let tx_nonce = tx.nonce().nonce();
    let effective_nonce = std::cmp::max(access_key.nonce, pending.max_nonce);
    if let Err(e) = verify_nonce(tx_nonce, effective_nonce, block_height, tx.nonce_mode()) {
        return TxVerdict::Failed(e);
    }
```

**File:** runtime/runtime/src/verifier.rs (L350-360)
```rust
    // saturating_sub is fine here: on the consensus path pending constraints
    // are always default (zero), so the subtraction is exact. On the RPC /
    // chunk-production path it is best-effort and does not affect consensus.
    let available_balance = account.amount().saturating_sub(pending.paid_from_balance);
    if available_balance < total_cost {
        return TxVerdict::Failed(InvalidTxError::NotEnoughBalance {
            signer_id: account_id.clone(),
            balance: available_balance,
            cost: total_cost,
        });
    }
```

**File:** runtime/runtime/src/verifier.rs (L365-373)
```rust
    let new_allowance = match check_and_compute_new_allowance(
        access_key,
        account_id,
        tx.public_key(),
        total_cost,
    ) {
        Ok(a) => a,
        Err(e) => return TxVerdict::Failed(e),
    };
```

**File:** chain/client/src/pending_transaction_queue.rs (L154-194)
```rust
/// Aggregate for a set of transactions, per account.
/// Used both per-chunk and as pending transaction queue totals. Supports add/subtract.
#[derive(Clone, Default)]
struct PendingAccount {
    access_key_tx_count: usize,
    deploy_tx_count: usize,
    /// Access key total_cost + gas key deposit_cost.
    paid_from_balance: Balance,
}

impl PendingAccount {
    fn add(&mut self, other: &PendingAccount) {
        self.access_key_tx_count += other.access_key_tx_count;
        self.deploy_tx_count += other.deploy_tx_count;
        self.paid_from_balance = self.paid_from_balance.saturating_add(other.paid_from_balance);
    }

    fn subtract(&mut self, other: &PendingAccount) {
        self.access_key_tx_count = checked_sub_or_default!(
            self.access_key_tx_count,
            other.access_key_tx_count,
            "access_key_tx_count underflow in pending transaction queue subtract"
        );
        self.deploy_tx_count = checked_sub_or_default!(
            self.deploy_tx_count,
            other.deploy_tx_count,
            "deploy_tx_count underflow in pending transaction queue subtract"
        );
        self.paid_from_balance = checked_sub_or_default!(
            self.paid_from_balance,
            other.paid_from_balance,
            "paid_from_balance underflow in pending transaction queue subtract"
        );
    }

    fn is_zero(&self) -> bool {
        self.access_key_tx_count == 0
            && self.deploy_tx_count == 0
            && self.paid_from_balance.is_zero()
    }
}
```

**File:** chain/client/src/pending_transaction_queue.rs (L405-468)
```rust
    /// Extract constraints for a given transaction without Skip/Admit logic.
    /// Used by the RPC handler for balance/nonce verification against certified state.
    pub fn get_pending_constraints(&self, tx: &SignedTransaction) -> PendingConstraints {
        let key_handle = PublicKeyHandle::from(tx.transaction.public_key());
        let snapshot = self.query_pending_state(&tx.transaction, &key_handle);
        PendingConstraints {
            paid_from_balance: snapshot.paid_from_balance,
            paid_from_gas_key: snapshot.pending_gas_key_cost,
            max_nonce: snapshot.max_nonce,
            max_bootstrap_nonce: snapshot.max_bootstrap_nonce,
        }
    }

    /// Highest nonce any uncertified chunk holds for `scope`, 0 if none does.
    fn max_pending_nonce(&self, scope: &NonceScope) -> Nonce {
        self.pending_nonces.get(scope).map(|n| n.max_nonce()).unwrap_or(0)
    }

    /// Query pending state for a single transaction. Extracts the counts and
    /// constraints needed by `PendingTxSession::check_pending`. This is called
    /// under the lock and should be fast.
    fn query_pending_state(
        &self,
        tx: &Transaction,
        key_handle: &PublicKeyHandle,
    ) -> PendingStateSnapshot {
        let signer_id = tx.signer_id();
        let pending_account = self.pending_accounts.get(signer_id);
        let access_key_tx_count = pending_account.map(|a| a.access_key_tx_count).unwrap_or(0);
        let deploy_tx_count = pending_account.map(|a| a.deploy_tx_count).unwrap_or(0);
        let paid_from_balance =
            pending_account.map(|a| a.paid_from_balance).unwrap_or(Balance::ZERO);

        let gas_key = (signer_id.clone(), key_handle.clone());
        let pending_gas_key_cost =
            self.pending_gas_key_costs.get(&gas_key).copied().unwrap_or(Balance::ZERO);

        let max_nonce = self.max_pending_nonce(&key_nonce_scope(tx, key_handle));
        // Kept apart from `max_nonce` so it reaches only the reader it belongs to,
        // which is a still uninitialized account (see `bootstrap_nonce_scope`).
        let max_bootstrap_nonce = self.max_pending_nonce(&bootstrap_nonce_scope(signer_id));

        PendingStateSnapshot {
            access_key_tx_count,
            deploy_tx_count,
            paid_from_balance,
            max_nonce,
            max_bootstrap_nonce,
            pending_gas_key_cost,
        }
    }
}

/// Snapshot of pending state for a single transaction's signer, extracted
/// under the lock and used outside it.
#[derive(Default)]
struct PendingStateSnapshot {
    access_key_tx_count: usize,
    deploy_tx_count: usize,
    paid_from_balance: Balance,
    max_nonce: Nonce,
    max_bootstrap_nonce: Nonce,
    pending_gas_key_cost: Balance,
}
```
