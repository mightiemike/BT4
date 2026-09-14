## Title
Attacker-donated gas-key balance permanently blocks key deletion and account deletion — ([File: runtime/runtime/src/access_keys.rs])

### Summary
`TransferToGasKeyAction` lets any predecessor deposit NEAR into an account's gas key without the account owner's consent. If the resulting `gas_key_info.balance` exceeds `GasKeyInfo::MAX_BALANCE_TO_BURN`, both `DeleteKeyAction` (for that specific key) and `DeleteAccountAction` (for the whole account, which sums *all* gas-key balances) hard-fail with `GasKeyBalanceTooHigh`, with no code path to reduce a gas key's balance below the threshold other than `WithdrawFromGasKeyAction`, which itself requires the key to still be usable/authorized. This mirrors the PartyDAO finding: an unpermissioned "donation" pushes an accounting value past a threshold used later to gate a critical, otherwise-legitimate state transition (deletion), permanently soft-locking that transition.

### Finding Description
`action_transfer_to_gas_key` in [1](#0-0)  increments `gas_key_info.balance` by the action's `deposit` with no upper bound check and no owner-permission check beyond the key existing — it operates on the *receiver* account of the receipt, not the signer, so any account can direct a `TransferToGasKeyAction` at another account's gas key.

`delete_gas_key` in [2](#0-1)  refuses to delete a gas key whenever `gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN`, returning `ActionErrorKind::GasKeyBalanceTooHigh` instead of deleting the key.

The same threshold gates whole-account deletion: `action_delete_account` sums *all* gas-key balances via `compute_gas_key_balance_sum` and aborts with the identical `GasKeyBalanceTooHigh` error if the sum exceeds `MAX_BALANCE_TO_BURN`, shown at [3](#0-2) .

This is the same bug class as the PartyDAO report: an unpermissioned external party ("donation") inflates a value that a later, otherwise-legitimate operation (buy finalization / key or account deletion) checks against a fixed threshold, and once that threshold is crossed the operation becomes permanently unavailable — there is no protocol path to force the balance back down without the cooperation of an action that itself is gated by the same threshold (deletion burns the balance, but deletion is exactly what's blocked). The account owner's only recourse is `WithdrawFromGasKeyAction`, which requires possessing the corresponding gas key's signing capability and is limited by `updated_balance = gas_key_info.balance.checked_sub(action.amount)`, i.e., withdrawal is possible in principle if the owner controls the key — this differs from the PartyDAO case where the party was *fully* softlocked. So the practical impact here is scoped to griefing a specific key/account-deletion flow rather than an irrecoverable freeze, unless the owner has lost or never had a usable signer for that gas key (e.g., a delegated/relayed gas key intended to be deleted by another party, or a key whose corresponding access is intentionally restricted).

### Impact Explanation
An attacker can send a small NEAR transfer via `TransferToGasKeyAction` to any account's gas key to push its balance above `GasKeyInfo::MAX_BALANCE_TO_BURN`. This:
- Permanently prevents that specific gas key from being deleted via `DeleteKeyAction` (`GasKeyBalanceTooHigh`).
- Permanently prevents the *entire account* from being deleted via `DeleteAccountAction` as long as any of its gas keys carries a balance sum above the threshold, since `action_delete_account` performs the same check across all gas keys on the account.

This can be used to grief victims who rely on account/key deletion for storage-stake reclamation, account migration, or cleanup flows (e.g., automated account rotation, relayer-managed gas keys). Because withdrawal requires possessing the gas key itself, an attacker targeting a gas key the victim does not control (e.g., a gas key issued to a third-party relayer with only spend permission) could create a genuinely irrecoverable lock on that key/account's deletion path.

### Likelihood Explanation
Trivial to execute: `TransferToGasKeyAction` requires only knowing the target account id and public key of an existing gas key (both are public, on-chain, discoverable data) and paying a minimal NEAR deposit slightly above `MAX_BALANCE_TO_BURN`. No special permission, gatekeeper bypass, or privileged role is needed — any single signed transaction from an ordinary account can trigger it.

### Recommendation
- Cap `action_transfer_to_gas_key` deposits so that the resulting gas-key balance cannot exceed `GasKeyInfo::MAX_BALANCE_TO_BURN` (reject or partially refund deposits that would push balance over the burn cap), or
- Allow `delete_gas_key`/`action_delete_account` to burn *any* balance up to a much higher/no cap (removing the incentive to treat "high balance" as a deletion blocker), or
- Restrict `TransferToGasKeyAction` so only the account itself (or an authorized actor) can fund its own gas keys.

### Proof of Concept
1. Victim account `victim.near` has a gas key `pk_gas` with `GasKeyInfo::balance = 0`.
2. Attacker signs and submits a transaction with predecessor `attacker.near`, receiver `victim.near`, action `TransferToGasKeyAction { public_key: pk_gas, deposit: MAX_BALANCE_TO_BURN + 1 }`. This executes via [4](#0-3)  with no cap check, setting `gas_key_info.balance` above the burn limit.
3. Victim (or anyone with access to `pk_gas`) later submits `DeleteKeyAction { public_key: pk_gas }`. The runtime hits the check at [5](#0-4)  and fails with `GasKeyBalanceTooHigh`, permanently blocking deletion of that key.
4. If the victim instead tries `DeleteAccountAction` on `victim.near`, `compute_gas_key_balance_sum` includes the inflated gas-key balance and the same error fires at [3](#0-2) , blocking account deletion as long as this gas key balance remains above the threshold.

**Note on confidence**: I was unable to fully trace whether there's an additional protocol-level restriction (e.g., a check that only the account's own actor/full-access key may execute `TransferToGasKeyAction` on itself) elsewhere in the receipt/action validation pipeline (`action_validation.rs`) that I did not have time to inspect in this session. If such a check exists, the "attacker" precondition would not hold for arbitrary third parties. I could not verify this file's content within the available tool budget, so this should be double-checked (`runtime/runtime/src/action_validation.rs`) before treating this as a confirmed finding.

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

**File:** runtime/runtime/src/actions.rs (L370-379)
```rust
    let gas_key_balance_to_burn = compute_gas_key_balance_sum(state_update, account_id)?;
    if gas_key_balance_to_burn > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: None,
            balance: gas_key_balance_to_burn,
        }
        .into());
        return Ok(());
    }
```
