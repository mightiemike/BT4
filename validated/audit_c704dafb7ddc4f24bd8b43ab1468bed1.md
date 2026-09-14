### Title
Missing allowance enforcement in gas-key transaction verification allows a restricted FunctionCall key to bypass its spending cap - (File: `runtime/runtime/src/verifier.rs`)

### Summary
`nearcore` supports "gas keys" — access keys that carry a `GasKeyInfo` (its own funded `balance`, spent by `TransferToGasKey`/`WithdrawFromGasKey`) and, like ordinary access keys, may also carry a `FunctionCallPermission` with a legacy `allowance` cap that is supposed to bound how much total value a restricted-privilege key can spend. The regular transaction-verification path enforces and decrements this `allowance`, but the gas-key transaction-verification path — a structurally parallel function meant to perform the equivalent checks for gas-key transactions — omits the allowance check entirely. This mirrors the reported bug class: a spending-restriction check implemented in one function (`deposit`/regular path) but never carried over to its sibling function (`withdraw`/gas-key path), letting the restricted actor act as if the cap does not exist.

### Finding Description
In the regular (non-gas-key) verification path, `verify_and_charge_tx_ephemeral` explicitly calls `check_and_compute_new_allowance` to subtract `total_cost` from the `FunctionCallPermission.allowance` and rejects the transaction with `NotEnoughAllowance` on underflow: [1](#0-0) 

`check_and_compute_new_allowance` itself is defined to specifically target `access_key.permission.function_call_permission()`'s `allowance` field: [2](#0-1) 

The gas-key transaction path, `verify_and_charge_gas_key_tx_ephemeral`, is the analogous "sibling" function used whenever a transaction is signed with a gas key (`nonce_index` present). It validates the gas key exists, validates the nonce index, checks nonce, checks the gas-key's own `balance` for `gas_cost`, and separately validates FunctionCall permission constraints (receiver/method/deposit) via `verify_function_call_permission` — but it never calls `check_and_compute_new_allowance`, so a finite `FunctionCallPermission.allowance` attached to a gas key is never checked or decremented: [3](#0-2) 

The `deposit_cost` portion of a gas-key transaction is drawn straight from the account's own `amount` (subject only to balance/storage-stake checks), with no allowance deduction applied at all: [4](#0-3) 

This is architecturally identical to the reported bug: the "spending cap" check (`allowance`) was implemented in one code path (regular access-key transactions) and is missing from the structurally equivalent path (gas-key transactions) that a caller can trivially route funds through instead.

### Impact Explanation
If an account grants a restricted `FunctionCallPermission` key a finite `allowance` (the mechanism the protocol uses to bound how much of the owner's account balance a low-trust key such as a dApp session key may spend) and that same access key is also provisioned as a gas key (carries `GasKeyInfo`), all transactions signed via the gas-key nonce path silently skip the allowance check. The `deposit_cost` for such a transaction is paid straight out of the owning account's `amount`, bounded only by the account's total balance and storage-staking requirement — not by the intended per-key `allowance` limit. This is an unauthorized-value-movement bug: it lets a key that the account owner explicitly capped move more of the account balance than the owner authorized, defeating the purpose of the `allowance` restriction, analogous to the vesting/lock bypass in the source report (a restriction correctly enforced on one action but omitted on its counterpart).

### Likelihood Explanation
The precondition — a `FunctionCallPermission` access key simultaneously carrying `GasKeyInfo` with a finite `allowance` — depends on whether `AddKeyAction` allows constructing such a key. The gas-key verifier code explicitly probes `access_key.permission.function_call_permission()` for a gas key access key, which implies this permission/gas-key combination is a reachable state in the current schema; this indicates the code was written expecting that a gas key could also carry a `FunctionCallPermission` (and therefore an `allowance`), making the missing check a genuine oversight rather than dead code. Exploitation requires nothing beyond an ordinary account owner granting itself (or a relayer/dApp) a gas key with a capped allowance and then issuing a normal, valid, correctly-signed gas-key transaction — a fully unprivileged action reachable by any transaction signer.

### Recommendation
Add the missing allowance check/decrement to `verify_and_charge_gas_key_tx_ephemeral`, mirroring `verify_and_charge_tx_ephemeral`: call `check_and_compute_new_allowance` against the `deposit_cost` (and/or total cost charged to the account) before accepting a gas-key transaction whose access key carries a `FunctionCallPermission` with a finite `allowance`, and propagate the updated allowance into the resulting `AccessKeyUpdate` the same way the regular path does. Add regression tests analogous to the existing gas-key balance tests (`runtime/runtime/src/access_keys.rs` tests) that specifically construct a gas key with a `FunctionCallPermission.allowance` and assert the allowance is enforced and decremented across gas-key transactions.

### Proof of Concept
Conceptual reproduction (not run, since this environment provides read-only code access):
1. Account `alice` adds an access key with `AccessKeyPermission::FunctionCall(FunctionCallPermission { allowance: Some(small_amount), receiver_id: "app.near", method_names: [...] })` and additionally provisions it as a gas key (`GasKeyInfo`), then funds the gas key balance via `TransferToGasKey`.
2. `alice` submits a `FunctionCall` transaction to `app.near` through the gas-key nonce path repeatedly, each with a non-zero `deposit`.
3. Each transaction is verified by `verify_and_charge_gas_key_tx_ephemeral`, which checks gas cost against `GasKeyInfo.balance` and deposit cost against `account.amount()` — but never checks or decrements `FunctionCallPermission.allowance`.
4. Total deposits sent over time exceed the `allowance` that was supposed to cap this key's spending; a transaction sent via the regular (non-gas-key) path with the same access key and same cumulative spend would have been rejected with `NotEnoughAllowance` per `check_and_compute_new_allowance` (`runtime/runtime/src/verifier.rs:282-303`), demonstrating the inconsistency and the bypass.

### Citations

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

**File:** runtime/runtime/src/verifier.rs (L524-619)
```rust
pub fn verify_and_charge_gas_key_tx_ephemeral(
    config: &RuntimeConfig,
    account: &Account,
    access_key: &AccessKey,
    current_nonce: Nonce,
    tx: &Transaction,
    transaction_cost: &TransactionCost,
    block_height: Option<BlockHeight>,
    pending: &PendingConstraints,
) -> TxVerdict {
    // It's the caller's responsibility to ONLY call this function for transactions with
    // nonce_index (i.e. gas key transactions).
    let Some(nonce_index) = tx.nonce().nonce_index() else {
        panic!("verify_and_charge_gas_key_tx_ephemeral called for non-gas key transaction")
    };
    let TransactionCost {
        gas_burnt,
        compute_burnt,
        gas_remaining,
        receipt_gas_price,
        burnt_amount,
        gas_cost,
        deposit_cost,
        ..
    } = *transaction_cost;
    let account_id = tx.signer_id();

    // Validate that access key is a gas key
    let Some(gas_key_info) = access_key.gas_key_info() else {
        return TxVerdict::Failed(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::AccessKeyNotFound {
                account_id: account_id.clone(),
                public_key: Box::new(tx.public_key().clone()),
            },
        ));
    };

    // Validate nonce_index is in valid range
    if nonce_index >= gas_key_info.num_nonces {
        return TxVerdict::Failed(InvalidTxError::InvalidNonceIndex {
            tx_nonce_index: Some(nonce_index),
            num_nonces: gas_key_info.num_nonces,
        });
    }

    let tx_nonce = tx.nonce().nonce();
    let effective_nonce = std::cmp::max(current_nonce, pending.max_nonce);
    if let Err(e) = verify_nonce(tx_nonce, effective_nonce, block_height, tx.nonce_mode()) {
        return TxVerdict::Failed(e);
    }

    // Check gas key has enough balance for gas costs, accounting for
    // pending gas key costs (prior gas key txs + pending WithdrawFromGasKey).
    // Unlike account balance, gas key balance only changes through transactions
    // that PTQ explicitly tracks, so pending should never exceed the balance.
    let Some(available_gas_key_balance) =
        gas_key_info.balance.checked_sub(pending.paid_from_gas_key)
    else {
        tracing::error!(
            target: "runtime",
            balance = %gas_key_info.balance,
            paid_from_gas_key = %pending.paid_from_gas_key,
            "pending gas key costs exceed gas key balance"
        );
        return TxVerdict::Failed(InvalidTxError::NotEnoughGasKeyBalance {
            signer_id: account_id.clone(),
            balance: Balance::ZERO,
            cost: gas_cost,
        });
    };
    if available_gas_key_balance < gas_cost {
        return TxVerdict::Failed(InvalidTxError::NotEnoughGasKeyBalance {
            signer_id: account_id.clone(),
            balance: available_gas_key_balance,
            cost: gas_cost,
        });
    }
    let new_gas_key_balance = gas_key_info.balance.checked_sub(gas_cost).unwrap();

    // Calculate new key balance in case of deposit failure. Charges only for the gas burned on
    // converting the transaction to a receipt.
    let Some(new_key_balance_on_deposit_failure) = gas_key_info.balance.checked_sub(burnt_amount)
    else {
        return TxVerdict::Failed(InvalidTxError::NotEnoughGasKeyBalance {
            signer_id: account_id.clone(),
            balance: gas_key_info.balance,
            cost: burnt_amount,
        });
    };

    // Validate FunctionCall permission constraints if applicable
    if let Some(function_call_permission) = access_key.permission.function_call_permission()
        && let Err(e) = verify_function_call_permission(function_call_permission, tx)
    {
        return TxVerdict::Failed(e);
    }
```

**File:** runtime/runtime/src/verifier.rs (L639-675)
```rust
    // Check account has enough balance for deposits, accounting for
    // pending balance costs from prior txs. saturating_sub is fine: on the
    // consensus path pending constraints are always default (zero), so the
    // subtraction is exact. On the RPC / chunk-production path it is
    // best-effort.
    let available_balance = account.amount().saturating_sub(pending.paid_from_balance);
    if available_balance < deposit_cost {
        return TxVerdict::DepositFailed {
            result: make_deposit_failed_result(account.amount()),
            error: InvalidTxError::NotEnoughBalanceForDeposit {
                signer_id: account_id.clone(),
                balance: available_balance,
                cost: deposit_cost,
                reason: DepositCostFailureReason::NotEnoughBalance,
            },
        };
    }
    // Debit only this tx's deposit cost, not the pending amount.
    let new_account_amount = account.amount().checked_sub(deposit_cost).unwrap();

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
