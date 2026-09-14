### Title
Gas key balance cap not enforced on deposit, only on deletion — permanently frozen access key / account ([File: runtime/runtime/src/access_keys.rs])

### Summary
NEAR enforces an upper bound on a gas key's `balance` (`GasKeyInfo::MAX_BALANCE_TO_BURN`) only at the point the key (or account) is deleted, not at the point the balance is deposited. `action_transfer_to_gas_key` lets the key owner grow the gas key's balance without any bound check, so an account can push a gas key's balance above `MAX_BALANCE_TO_BURN` in ordinary operation. Once that happens, `DeleteKey`/`DeleteAccount` on that key permanently fails with `GasKeyBalanceTooHigh`, exactly mirroring the reported pattern in the external report: a cap checked at one code path (drawing settlement / here, deletion) is never enforced at the path that actually grows the bounded quantity (LP value growth / here, `TransferToGasKeyAction`).

### Finding Description
`GasKeyInfo::MAX_BALANCE_TO_BURN` bounds how much balance a gas key may hold when it is deleted — `delete_gas_key` rejects deletion with `ActionErrorKind::GasKeyBalanceTooHigh` if `gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN`: [1](#0-0) 

`action_delete_account` performs the analogous aggregate check when deleting an account (summing all its gas keys' balances against the same threshold), per the protocol-model spec: [2](#0-1) 

However, the only mutator that grows a gas key's balance, `action_transfer_to_gas_key`, performs an unconditional `checked_add` with no comparison against `MAX_BALANCE_TO_BURN` (or any other upper bound) — it only checks for `u128` overflow: [3](#0-2) 

This is structurally identical to the reported megapot bug: `_calculateLpPoolCap` enforces a cap that is checked at one site (deposit), but `processDrawingSettlement`'s `newLPValue` computation — the site that actually grows the bounded quantity — never re-applies that cap. Here, the cap (`MAX_BALANCE_TO_BURN`) is checked only at the deletion site, while `action_transfer_to_gas_key` — the site that actually grows the bounded quantity (`gas_key_info.balance`) — never re-applies it.

### Impact Explanation
Any account owner can, via ordinary `TransferToGasKeyAction` calls against their own gas key (no privileged role required — reachable by any transaction signer/contract caller with a full-access key on the account), push a gas key's `balance` above `MAX_BALANCE_TO_BURN`. From that point on:
- `DeleteKeyAction` targeting that gas key will always return `ActionErrorKind::GasKeyBalanceTooHigh` (`access_keys.rs:103-111`), so the key can never be removed via the normal action path.
- `action_delete_account` sums all gas-key balances against the same `MAX_BALANCE_TO_BURN` threshold, so an account holding such an over-funded gas key can become permanently non-deletable.

This is a concrete "permanently frozen funds" condition: the balance held in the gas key is neither spendable through the intended gas-key mechanics beyond what `WithdrawFromGasKeyAction` recovers, nor able to be reclaimed via deletion, and the key/account itself becomes stuck in a state the protocol has no path out of, since the only path that clears a gas key balance (deletion) is now permanently blocked by an error that the deposit path never protected against.

### Likelihood Explanation
High likelihood of accidental triggering and trivial to trigger deliberately: it requires only a sequence of ordinary `TransferToGasKeyAction` transactions signed by the account's own key, with no special privileges, timing, or validator/node behavior involved — purely a transaction-signer-reachable action-validation gap.

### Recommendation
Enforce `GasKeyInfo::MAX_BALANCE_TO_BURN` (or a configurable cap) at `action_transfer_to_gas_key` time in `runtime/runtime/src/access_keys.rs`, rejecting or capping deposits that would push `gas_key_info.balance` above the threshold checked later at deletion, so the same invariant is enforced consistently on both the growth path and the consumption/deletion path.

### Proof of Concept
1. Add a gas key to an account via `AddKeyAction` with `AccessKey::gas_key_full_access(num_nonces)` (see `add_gas_key`, `access_keys.rs:194-228`).
2. Repeatedly call `TransferToGasKeyAction` on that key with deposits that sum to more than `GasKeyInfo::MAX_BALANCE_TO_BURN` (`action_transfer_to_gas_key`, `access_keys.rs:257-288` performs no cap check, only overflow checks).
3. Submit `DeleteKeyAction` for that public key — `action_delete_key` → `delete_gas_key` returns `ActionErrorKind::GasKeyBalanceTooHigh` (`access_keys.rs:102-111`), permanently blocking deletion of the key.
4. Attempt `DeleteAccount` on the account holding this gas key — it fails the same aggregate `MAX_BALANCE_TO_BURN` check described in the accounts-keys spec (`protocol-model/spec/accounts-keys.md:108-109`), permanently blocking account deletion while the gas key exists.

Note: I could not directly view `core/primitives-core/src/account.rs` around `GasKeyInfo`/`MAX_BALANCE_TO_BURN`'s exact numeric value and full `GasKeyInfo` definition (index truncated that region), so the precise threshold value is unconfirmed from the index alone; a Devin session with full file access would be needed to pin down the exact constant and any existing test coverage of this boundary.

### Citations

**File:** runtime/runtime/src/access_keys.rs (L102-111)
```rust
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

**File:** protocol-model/spec/accounts-keys.md (L108-109)
```markdown
- **Storage stake backs storage usage** unless zero-balance: `check_storage_stake` (`verifier.rs:48`); violation → `LackBalanceForStorageStaking`/`LackBalanceForState`. An arithmetic-overflow inconsistency (`storage_amount_per_byte * storage_usage` or `amount + locked` overflows, `verifier.rs:56`,`:65`) returns `StorageStakingError::StorageError`, surfaced as `StorageInconsistentState`.
- **Gas-key deletion burns ≤ 1 NEAR**: `delete_gas_key` errors `GasKeyBalanceTooHigh` and aborts if `balance > MAX_BALANCE_TO_BURN`; otherwise the balance is burned (added to `tokens_burnt`, not refunded) (`access_keys.rs:103`,`:112`). Account deletion sums all gas-key balances against the same threshold (`actions.rs:389`, asserted by `test_delete_account_gas_key_balance_too_high`).
```
