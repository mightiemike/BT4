### Title
Griefing DOS on gas-key deletion / account deletion via unauthorized `TransferToGasKey` inflation of `GasKeyInfo.balance` above `MAX_BALANCE_TO_BURN` - ([File: runtime/runtime/src/actions.rs])

### Summary
`check_actor_permissions` explicitly allows `Action::TransferToGasKey` to be executed by *any* predecessor account, not just the account owner, while `DeleteKey`/`WithdrawFromGasKey`/`DeleteAccount` require `actor_id == account_id`. Any unprivileged signer can therefore submit a transaction/receipt containing a `TransferToGasKeyAction` that deposits NEAR into a victim's existing gas key, inflating `GasKeyInfo.balance` above the hard-coded `GasKeyInfo::MAX_BALANCE_TO_BURN` (1 NEAR) threshold. Once above that threshold, both `delete_gas_key` (single-key deletion, reached via `DeleteKey`) and `action_delete_account` (whole-account deletion) unconditionally reject the operation with `GasKeyBalanceTooHigh`, exactly mirroring the reported dTRINITY pattern where an attacker sends 1 wei of an asset to force a `balance > 0` guard to permanently revert a privileged removal function.

### Finding Description
`action_transfer_to_gas_key` (`runtime/runtime/src/access_keys.rs:257-288`) simply looks up the gas key on `account_id` and does `gas_key_info.balance = gas_key_info.balance.checked_add(action.deposit)` — there is no check that the caller (predecessor/actor) is the account owner.

The permission gate that would normally block a stranger from mutating another account's keys is `check_actor_permissions` (`runtime/runtime/src/actions.rs:711-757`). It requires `actor_id == account_id` for `DeployContract`, `Stake`, `AddKey`, `DeleteKey`, `DeployGlobalContract`, `UseGlobalContract`, `WithdrawFromGasKey`, and `DeleteAccount` — but `Action::TransferToGasKey(_)` is explicitly placed in the "no restriction" arm alongside `CreateAccount`/`FunctionCall`/`Transfer` (`runtime/runtime/src/actions.rs:749-752`):
```
Action::CreateAccount(_)
| Action::FunctionCall(_)
| Action::Transfer(_)
| Action::TransferToGasKey(_) => (),
```
This means anyone can send NEAR into any account's *existing* gas key balance, just like anyone can send a plain `Transfer`.

Both deletion paths gate on the resulting balance:
- `delete_gas_key` (`runtime/runtime/src/access_keys.rs:93-111`): `if gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN { ... GasKeyBalanceTooHigh ...; return Ok(()) }` — the key is left intact and `DeleteKey` fails.
- `action_delete_account` (`runtime/runtime/src/actions.rs:339-348`): sums all gas-key balances via `compute_gas_key_balance_sum`; `if gas_key_balance_to_burn > GasKeyInfo::MAX_BALANCE_TO_BURN { ... GasKeyBalanceTooHigh ...; return Ok(()) }` — `DeleteAccount` fails and the account (and its remaining refundable balance to `beneficiary_id`) cannot be removed.

`MAX_BALANCE_TO_BURN` is fixed at 1 NEAR (`core/primitives-core/src/account.rs:554`), a small, attacker-affordable amount, directly analogous to the "send 1 wei" griefing vector in the report.

### Impact Explanation
An unprivileged third party who observes (via RPC `view_access_key`/`view_access_key_list`) that a target account has a gas key can submit a single `TransferToGasKeyAction` depositing slightly over 1 NEAR into that key. This:
- Blocks `DeleteKey` on that specific gas key (`GasKeyBalanceTooHigh`, `runtime/runtime/src/access_keys.rs:103-111`).
- Blocks `DeleteAccount` entirely for the whole account while any gas key's balance sum exceeds the threshold (`runtime/runtime/src/actions.rs:340-348`), preventing the owner from recovering their storage-staked balance/refund to `beneficiary_id` until they take remedial action.

The victim is not helplessly locked forever: `action_withdraw_from_gas_key` *does* require `actor_id == account_id` (self-only), so the account owner can call `WithdrawFromGasKey` to pull the balance back down below the threshold before retrying deletion. This makes the DOS self-recoverable rather than permanent, but it still forces an unplanned, attacker-triggered mitigation step on the victim and can disrupt automated flows (e.g., relayer/meta-tx account cleanup scripts) that assume `DeleteKey`/`DeleteAccount` succeed unconditionally — a Medium-severity griefing DOS consistent with the class of the referenced report.

### Likelihood Explanation
Trivial to execute: it requires only discovering an existing gas-key public key on the target account (public on-chain data) and submitting one ordinary signed transaction with a `TransferToGasKeyAction` and a deposit slightly above 1 NEAR from the attacker's own funds. No special privileges, races, or validator collusion needed — any RPC caller/transaction signer can do this.

### Recommendation
Restrict `TransferToGasKeyAction` in `check_actor_permissions` (`runtime/runtime/src/actions.rs:711-757`) to require `actor_id == account_id`, mirroring `WithdrawFromGasKey`/`AddKey`/`DeleteKey`. If third-party funding of a gas key is an intended feature (e.g., a sponsor topping up a user's gas key), instead decouple the deletion-blocking threshold from attacker-controllable balance — e.g., cap/clamp the amount actually burned at `MAX_BALANCE_TO_BURN` and refund/return any excess to the depositor or to the account's own balance rather than rejecting the deletion outright.

### Proof of Concept
1. Victim account `alice.near` has an existing full-access gas key with public key `PK` and balance `B ≤ 1 NEAR` (`AccessKey::gas_key_full_access`, seen in `test-loop-tests/src/tests/gas_keys.rs`).
2. Attacker (any funded account, e.g. `mallory.near`) submits:
```
SignedTransaction::from_actions(
    nonce, mallory.near, alice.near, &mallory_signer,
    vec![Action::TransferToGasKey(Box::new(TransferToGasKeyAction {
        public_key: PK,
        deposit: Balance::from_near(1) + Balance::from_yoctonear(1), // > MAX_BALANCE_TO_BURN
    }))],
    block_hash,
)
```
This succeeds because `check_actor_permissions` does not gate `TransferToGasKey` on `actor_id == account_id` (`runtime/runtime/src/actions.rs:749-752`), and `action_transfer_to_gas_key` performs no ownership check (`runtime/runtime/src/access_keys.rs:257-288`).
3. `alice.near` (or a relayer acting for her) now submits `DeleteKey { public_key: PK }` (or `DeleteAccount`). Both fail with `ActionErrorKind::GasKeyBalanceTooHigh` (`runtime/runtime/src/access_keys.rs:103-111`, `runtime/runtime/src/actions.rs:340-347`), unit-tested analogously in `test_delete_gas_key_balance_too_high` and `test_delete_account_gas_key_balance_too_high` (`runtime/runtime/src/access_keys.rs:1218-1332`) but there triggered by the account's own deposits, not by an unauthorized third party.
4. `alice.near` must additionally submit `WithdrawFromGasKey` to bring the balance back under 1 NEAR before retrying deletion — an attacker-forced remediation step confirming the griefing DOS. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** runtime/runtime/src/actions.rs (L339-348)
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

**File:** runtime/runtime/src/actions.rs (L711-757)
```rust
pub(crate) fn check_actor_permissions(
    action: &Action,
    account: &Option<Account>,
    actor_id: &AccountId,
    account_id: &AccountId,
) -> Result<(), ActionError> {
    match action {
        Action::DeployContract(_)
        | Action::Stake(_)
        | Action::AddKey(_)
        | Action::DeleteKey(_)
        | Action::DeployGlobalContract(_)
        | Action::UseGlobalContract(_)
        | Action::WithdrawFromGasKey(_) => {
            if actor_id != account_id {
                return Err(ActionErrorKind::ActorNoPermission {
                    account_id: account_id.clone(),
                    actor_id: actor_id.clone(),
                }
                .into());
            }
        }
        Action::DeleteAccount(_) => {
            if actor_id != account_id {
                return Err(ActionErrorKind::ActorNoPermission {
                    account_id: account_id.clone(),
                    actor_id: actor_id.clone(),
                }
                .into());
            }
            let account = account.as_ref().unwrap();
            if !account.locked().is_zero() {
                return Err(ActionErrorKind::DeleteAccountStaking {
                    account_id: account_id.clone(),
                }
                .into());
            }
        }
        Action::CreateAccount(_)
        | Action::FunctionCall(_)
        | Action::Transfer(_)
        | Action::TransferToGasKey(_) => (),
        Action::Delegate(_) | Action::DelegateV2(_) => (),
        Action::DeterministicStateInit(_) => (),
    };
    Ok(())
}
```

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

**File:** protocol-model/spec/accounts-keys.md (L19-19)
```markdown
- **`GasKeyInfo`** — `core/primitives-core/src/account.rs:546` — `{ balance: Balance, num_nonces: NonceIndex }`. `balance` is a prepaid pot used to pay gas; `num_nonces` is the count of independent nonce slots. `MAX_BALANCE_TO_BURN = 1 NEAR` (`:554`) caps the balance that may be burned when deleting the key/account.
```
