### Title
Insolvent account cannot submit its own repair transaction because storage-stake admission check uses pre-execution storage usage — ([File: runtime/runtime/src/verifier.rs])

### Summary
`verify_and_charge_tx_ephemeral` (the transaction-admission/ephemeral-verification step that runs before any of the transaction's actions are executed) rejects a transaction with `InvalidTxError::LackBalanceForState` if the signer account's *current* balance cannot cover its *current* `storage_usage()`. This check runs against the account's pre-execution state, so a transaction whose own actions (e.g. `DeleteKey`) would reduce `storage_usage()` enough to restore solvency is rejected before it ever gets the chance to execute those actions. An account that becomes insolvent (balance < storage cost) — which nearcore's own documentation acknowledges can happen "in case it gets slashed" — is permanently unable to submit any transaction, including the one that would fix it, and remains frozen until an unrelated third party sends it extra funds.

### Finding Description
`check_storage_stake` (`runtime/runtime/src/verifier.rs:48-84`) compares `account.storage_usage()` against `account.amount() + account.locked()` and returns `LackBalanceForStorageStaking` if insufficient (unless the account is a NEP-448 zero-balance account, `storage_usage() <= ZERO_BALANCE_ACCOUNT_STORAGE_LIMIT = 770`).

This check is invoked twice:
1. **Pre-execution, at tx admission** — `verify_and_charge_tx_ephemeral` (`runtime/runtime/src/verifier.rs:312-404`) calls `check_storage_stake(account, new_amount, config)` at line 375, where `account` is the signer's account fetched from state *before* any action in the transaction runs, and `new_amount` only reflects the gas/deposit cost being debited — it does **not** reflect any storage-usage change the transaction's own actions would cause. If this fails, the whole transaction is rejected as `InvalidTxError::LackBalanceForState` and never becomes a receipt ( [1](#0-0) ).
2. **Post-execution, at receipt apply** — `runtime/runtime/src/lib.rs:1009-1030` re-checks `check_storage_stake` after actions have executed, using the account's *updated* `storage_usage()`. This later check would correctly recognize that a `DeleteKey`/similar action fixed the debt.

Because gate (1) runs first and uses stale state, an insolvent account can never reach gate (2): its repair transaction is rejected at admission regardless of what actions it contains. `nearcore`'s own `docs/Economics/Economics.md` acknowledges the resulting failure mode explicitly: "Account can end up with not enough balance in case it gets slashed. Account will become unusable as all originating transactions will fail (including deletion). The only way to recover it in this case is by sending extra funds from a different accounts" ( [2](#0-1) ). The documented pseudocode even models an exemption for `DeleteAccount` actions ("If enough balance OR account is been deleted by the owner", [3](#0-2) ), but the actual implementation in `verify_and_charge_tx_ephemeral` contains no such exemption for `DeleteAccount` or any other self-repairing action ( [4](#0-3) ) — the code diverges from the documented intended behavior.

The gas-key path (`verify_and_charge_gas_key_tx_ephemeral`) has the identical structure, calling `check_storage_stake` on the pre-execution account before the deposit-bearing action runs ( [5](#0-4) ).

This is directly analogous to the Cozy Finance issue: a "set owner" (here, the account owner/signer) becomes naturally insolvent through ordinary protocol operation (slashing, storage growth, gas-key burns), and the very mechanism meant to let them repair the situation (a transaction containing actions that reduce their debt) is blocked by a solvency check that only looks at stale, pre-repair state — leaving them permanently stuck unless a third party bails them out.

### Impact Explanation
An affected account's funds become **permanently frozen** from the account owner's own perspective: they cannot sign any transaction — including one designed purely to reduce their own storage usage (e.g. `DeleteKey`, `DeleteAccount` combined with other actions in the same tx) — because the pre-execution admission check rejects it outright. The account is unusable until an unrelated account sends it a top-up transfer, which the owner has no guaranteed way to obtain. This matches the "permanently frozen funds" acceptance criterion: legitimate value (the account's balance/locked stake) is rendered inaccessible to its own controller through an ordinary, non-adversarial state transition (e.g., slashing).

### Likelihood Explanation
This requires no attacker and no coordination — it is triggered by ordinary protocol mechanics that legitimately reduce an account's balance relative to its storage usage, such as slashing (docs explicitly cite this), or state growth outpacing balance over time. Once triggered, the account is deterministically stuck: every transaction it signs runs through `verify_and_charge_tx_ephemeral`/`verify_and_charge_gas_key_tx_ephemeral`, both of which apply the stale-state check unconditionally before any action executes.

### Recommendation
Either (a) implement the exemption documented in `docs/Economics/Economics.md` by allowing transactions containing `DeleteAccount` (or more generally, transactions whose actions are known to only reduce storage usage, like `DeleteKey`) to bypass or defer the pre-execution `check_storage_stake` in `verify_and_charge_tx_ephemeral`/`verify_and_charge_gas_key_tx_ephemeral`, letting the post-execution check in `runtime/runtime/src/lib.rs` be the authoritative gate; or (b) explicitly special-case storage-reducing actions during admission so a repair transaction reaches execution before being judged on solvency.

### Proof of Concept
1. Create an account with several access keys / large state such that `storage_usage()` is just above what its balance backs (e.g., via the pattern in `runtime/runtime/src/verifier.rs` test `test_validate_transaction_invalid_low_balance_many_keys`, `runtime/runtime/src/verifier.rs:1643-1693`, which already demonstrates a `send_money` transaction being rejected with `InvalidTxError::LackBalanceForState` purely because of stale storage accounting).
2. From that same account, instead submit a `DeleteKey` transaction targeting one of its own access keys — an action that, if executed, would reduce `storage_usage()` enough to satisfy `check_storage_stake`.
3. Observe that `validate_verify_and_charge_transaction` → `verify_and_charge_tx_ephemeral` still rejects the transaction with `InvalidTxError::LackBalanceForState` at `runtime/runtime/src/verifier.rs:375-386`, because the check is evaluated against the account's *pre-execution* `storage_usage()`, never allowing the `DeleteKey` action to run and fix the shortfall.
4. The account remains stuck in this state indefinitely until an external account sends it a balance top-up transaction.

### Citations

**File:** runtime/runtime/src/verifier.rs (L312-386)
```rust
pub fn verify_and_charge_tx_ephemeral(
    config: &RuntimeConfig,
    account: &Account,
    access_key: &AccessKey,
    tx: &Transaction,
    transaction_cost: &TransactionCost,
    block_height: Option<BlockHeight>,
    pending: &PendingConstraints,
) -> TxVerdict {
    // It's the caller's responsibility to NOT call this function for transactions with
    // nonce_index (i.e. gas key transactions).
    assert!(
        tx.nonce().nonce_index().is_none(),
        "verify_and_charge_tx_ephemeral called for gas key transaction"
    );
    // Gas keys must be used via gas key transaction path (with nonce_index)
    if let Some(gas_key_info) = access_key.gas_key_info() {
        return TxVerdict::Failed(InvalidTxError::InvalidNonceIndex {
            tx_nonce_index: None,
            num_nonces: gas_key_info.num_nonces,
        });
    }
    let TransactionCost {
        gas_burnt,
        compute_burnt,
        gas_remaining,
        receipt_gas_price,
        total_cost,
        burnt_amount,
        ..
    } = *transaction_cost;
    let account_id = tx.signer_id();
    let tx_nonce = tx.nonce().nonce();
    let effective_nonce = std::cmp::max(access_key.nonce, pending.max_nonce);
    if let Err(e) = verify_nonce(tx_nonce, effective_nonce, block_height, tx.nonce_mode()) {
        return TxVerdict::Failed(e);
    }

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
    // Debit only this tx's cost, not the pending amount (which was already
    // charged in prior chunks and will be applied at execution time).
    let new_amount = account.amount().checked_sub(total_cost).unwrap();

    let new_allowance = match check_and_compute_new_allowance(
        access_key,
        account_id,
        tx.public_key(),
        total_cost,
    ) {
        Ok(a) => a,
        Err(e) => return TxVerdict::Failed(e),
    };

    match check_storage_stake(account, new_amount, config) {
        Ok(()) => {}
        Err(StorageStakingError::LackBalanceForStorageStaking(amount)) => {
            return TxVerdict::Failed(InvalidTxError::LackBalanceForState {
                signer_id: account_id.clone(),
                amount,
            });
        }
        Err(StorageStakingError::StorageError(err)) => {
            return TxVerdict::Failed(StorageError::StorageInconsistentState(err).into());
        }
    };
```

**File:** runtime/runtime/src/verifier.rs (L659-675)
```rust
    match check_storage_stake(account, new_account_amount, config) {
        Ok(()) => {}
        Err(StorageStakingError::LackBalanceForStorageStaking(amount)) => {
            return TxVerdict::DepositFailed {
                result: make_deposit_failed_result(account.amount()),
                error: InvalidTxError::NotEnoughBalanceForDeposit {
                    signer_id: account_id.clone(),
                    balance: new_account_amount,
                    cost: amount,
                    reason: DepositCostFailureReason::LackBalanceForState,
                },
            };
        }
        Err(StorageStakingError::StorageError(err)) => {
            return TxVerdict::Failed(StorageError::StorageInconsistentState(err).into());
        }
    };
```

**File:** docs/Economics/Economics.md (L84-87)
```markdown
    result = check_storage_cost(signer_account)
    # If enough balance OR account is been deleted by the owner.
    if not result.ok() or DeleteAccount(tx.signer_id) in tx.actions:
        assert LackBalanceForState(signer_id: tx.signer_id, amount: result.err())
```

**File:** docs/Economics/Economics.md (L100-101)
```markdown
Account can end up with not enough balance in case it gets slashed. Account will become unusable as all originating transactions will fail (including deletion).
The only way to recover it in this case is by sending extra funds from a different accounts.
```
