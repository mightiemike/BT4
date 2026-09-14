### Title
Gas key balance cap (`GasKeyInfo::MAX_BALANCE_TO_BURN`) is only enforced on `DeleteKey`, not at deposit time in `action_transfer_to_gas_key` - ([File: runtime/runtime/src/access_keys.rs])

### Summary
The JOJO report describes a per-account maximum deposit that is checked only *after* the funds have already been deposited, letting the cap be silently exceeded. The analogous pattern in nearcore is the gas-key balance cap `GasKeyInfo::MAX_BALANCE_TO_BURN`: it is validated only inside `delete_gas_key` (invoked from `action_delete_key`) at key-deletion time, never inside `action_transfer_to_gas_key`, which is the function that actually increases the gas key's balance.

### Finding Description
`action_transfer_to_gas_key` in `runtime/runtime/src/access_keys.rs` (lines 257-288) unconditionally adds the deposit to `gas_key_info.balance` with no upper-bound check: [1](#0-0) 

The only place `MAX_BALANCE_TO_BURN` is checked is in `delete_gas_key`, called from `action_delete_key` when the key being deleted turns out to be a gas key: [2](#0-1) 

This means an account owner (or a contract acting via `promise_batch_action_transfer_to_gas_key`, verified in `runtime/near-vm-runner/src/logic/tests/promises.rs:297-333`) can call `TransferToGasKey` repeatedly (or once with a very large `deposit`) to push a gas key's balance arbitrarily far above `MAX_BALANCE_TO_BURN`. The verifier-side check (`verify_and_charge_gas_key_tx_ephemeral` in `runtime/runtime/src/verifier.rs`) only validates that the *signer's account* has enough balance to cover the deposit (`NotEnoughBalanceForDeposit`), never that the resulting gas-key balance stays under the burn cap: [3](#0-2) 

Confirmed by the existing test `test_transfer_to_gas_key_success`, which shows balance accumulating without any limit check across multiple transfers: [4](#0-3) 

And by `test_delete_account_gas_key_balance_too_high`, which proves the cap is only enforced when the key is later deleted: [5](#0-4) 

This is precisely the "check happens after the funds are already in" bug pattern from the JOJO report: the limit-enforcing logic (`GasKeyBalanceTooHigh`) lives in a completely different code path (deletion) than the value-increasing logic (deposit/transfer), so the cap can be trivially bypassed for as long as the key is not deleted.

### Impact Explanation
`MAX_BALANCE_TO_BURN` exists specifically because `delete_gas_key` burns (destroys, not refunds) any remaining gas-key balance on deletion — it is a safety limit meant to bound how much NEAR can be irrecoverably destroyed by a single `DeleteKey` action. Because deposits are unbounded, an account can accumulate a gas-key balance far in excess of the intended cap. This does not by itself let anyone steal funds, but it defeats the protocol's designed limit on burnable balance per gas key and could interact with fee/gas accounting or future gas-key features that assume the cap holds (e.g., future logic bounding potential inflation-adjustment side effects of large burns). Given the cap is a named protocol invariant enforced in one specific action but bypassable via a different, more commonly used action, this is a genuine invariant violation in production runtime code reachable directly by any unprivileged account owner or contract via a signed transaction or promise batch action.

### Likelihood Explanation
Trivially reachable: any account holder can add a gas key (`AddKeyAction` with `GasKeyInfo`) and then call `TransferToGasKey` (or the equivalent host function `promise_batch_action_transfer_to_gas_key`) as many times as their account balance allows — no special permissions, no reentrancy, no race condition required. This is a straightforward, deterministic sequence of two standard actions available to every user.

### Recommendation
Enforce `GasKeyInfo::MAX_BALANCE_TO_BURN` (or a configurable analogous cap) at the point where the gas-key balance is increased, i.e., inside `action_transfer_to_gas_key` (and any host-function equivalent), rejecting or capping deposits that would push `gas_key_info.balance` above the limit, rather than only checking the limit at deletion time.

### Proof of Concept
1. Account `alice` funds an account with sufficient balance.
2. `alice` submits `AddKeyAction` adding a gas key (`AccessKey::gas_key_full_access(n)`).
3. `alice` submits repeated `TransferToGasKeyAction`s (or a single one with `deposit` far exceeding `GasKeyInfo::MAX_BALANCE_TO_BURN`) targeting that gas key — each call succeeds unconditionally per `action_transfer_to_gas_key` [6](#0-5) .
4. Query the gas key via RPC: `gas_key_info.balance` is now well above `MAX_BALANCE_TO_BURN`.
5. Attempting to `DeleteKey` on this gas key now fails with `GasKeyBalanceTooHigh` [7](#0-6) , demonstrating the cap was already violated well before the enforcement point — confirming the check-after-effect bypass.

### Citations

**File:** runtime/runtime/src/access_keys.rs (L93-111)
```rust
fn delete_gas_key(
    config: &RuntimeConfig,
    state_update: &mut TrieUpdate,
    account: &mut Account,
    result: &mut ActionResult,
    account_id: &AccountId,
    public_key: &PublicKey,
    access_key: &AccessKey,
    gas_key_info: &GasKeyInfo,
) -> Result<(), RuntimeError> {
    if gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: Some(Box::new(public_key.clone())),
            balance: gas_key_info.balance,
        }
        .into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/access_keys.rs (L257-288)
```rust
pub(crate) fn action_transfer_to_gas_key(
    state_update: &mut TrieUpdate,
    result: &mut ActionResult,
    account_id: &AccountId,
    action: &TransferToGasKeyAction,
) -> Result<(), RuntimeError> {
    let Some(mut access_key) = get_access_key(state_update, account_id, &action.public_key)? else {
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };
    let Some(gas_key_info) = access_key.gas_key_info_mut() else {
        // Key exists but is not a gas key
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };

    gas_key_info.balance = gas_key_info.balance.checked_add(action.deposit).ok_or_else(|| {
        RuntimeError::StorageError(StorageError::StorageInconsistentState(
            "gas key balance integer overflow".to_string(),
        ))
    })?;
    set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);
    Ok(())
}
```

**File:** runtime/runtime/src/access_keys.rs (L996-1020)
```rust
    #[test]
    fn test_transfer_to_gas_key_success() {
        let (account_id, public_key, access_key) = test_account_keys();
        let mut state_update = setup_account(&account_id, &public_key, &access_key);
        let mut account = get_account(&state_update, &account_id).unwrap().unwrap();

        let gas_key_public_key =
            InMemorySigner::from_seed(account_id.clone(), KeyType::ED25519, "gas_key").public_key();
        add_gas_key_to_account(&mut state_update, &mut account, &account_id, &gas_key_public_key);

        let deposit_amount = Balance::from_yoctonear(1_000_000);
        transfer_to_gas_key(&mut state_update, &account_id, &gas_key_public_key, deposit_amount);

        let gas_key =
            get_access_key(&state_update, &account_id, &gas_key_public_key).unwrap().unwrap();
        let gas_key_info = gas_key.gas_key_info().unwrap();
        assert_eq!(gas_key_info.balance, deposit_amount);

        // Transfer more and verify accumulation
        transfer_to_gas_key(&mut state_update, &account_id, &gas_key_public_key, deposit_amount);
        let gas_key =
            get_access_key(&state_update, &account_id, &gas_key_public_key).unwrap().unwrap();
        let gas_key_info = gas_key.gas_key_info().unwrap();
        assert_eq!(gas_key_info.balance, Balance::from_yoctonear(2_000_000));
    }
```

**File:** runtime/runtime/src/access_keys.rs (L1290-1332)
```rust
    #[test]
    fn test_delete_account_gas_key_balance_too_high() {
        let (account_id, public_key, access_key) = test_account_keys();
        let public_keys: Vec<PublicKey> = (0..3)
            .map(|i| PublicKey::from_seed(KeyType::ED25519, &format!("gas_key_{i}")))
            .collect();
        let mut state_update = setup_account(&account_id, &public_key, &access_key);
        let mut account = get_account(&state_update, &account_id).unwrap().unwrap();
        for public_key in &public_keys {
            add_gas_key_to_account(&mut state_update, &mut account, &account_id, public_key);
        }

        // Fund gas keys so total exceeds 1 NEAR
        let deposit_amounts = [
            Balance::from_millinear(400),
            Balance::from_millinear(400),
            Balance::from_millinear(201),
        ];
        for (pk, amount) in public_keys.iter().zip(deposit_amounts.iter()) {
            transfer_to_gas_key(&mut state_update, &account_id, pk, *amount);
        }
        state_update.commit(StateChangeCause::InitialState);

        let action_result = test_delete_account(
            &account_id,
            AccountContract::from_local_code_hash(CryptoHash::default()),
            100,
            PROTOCOL_VERSION,
            &mut state_update,
        );
        let expected_total =
            deposit_amounts.iter().fold(Balance::ZERO, |acc, x| acc.checked_add(*x).unwrap());
        assert_eq!(
            action_result.result,
            Err(ActionErrorKind::GasKeyBalanceTooHigh {
                account_id: account_id.clone(),
                public_key: None,
                balance: expected_total,
            }
            .into())
        );
        assert_eq!(action_result.tokens_burnt, Balance::ZERO);
    }
```

**File:** runtime/runtime/src/verifier.rs (L633-676)
```rust
    let make_success_result =
        move |new_account_amount| make_result(new_account_amount, new_gas_key_balance);
    let make_deposit_failed_result = move |new_account_amount| {
        make_result(new_account_amount, new_key_balance_on_deposit_failure)
    };

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
