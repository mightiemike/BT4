Confirmed: `Action::TransferToGasKey` requires no actor permission check (`check_actor_permissions` at `runtime/runtime/src/actions.rs:793-797` groups it with `CreateAccount`/`Transfer`, which are unrestricted — any predecessor can call it against any target account/public key), while `Action::DeleteKey`/`Action::WithdrawFromGasKey`/`Action::DeleteAccount` require `actor_id == account_id` (self-only). This is a strong structural analog to the reported bug class.

### Title
Permissionless `TransferToGasKey` griefing can front-run and permanently block gas-key/account deletion — ([File: runtime/runtime/src/access_keys.rs])

### Summary
`GasKeyBalanceTooHigh` guards `DeleteKey` and `DeleteAccount` from burning large gas-key balances during deletion. Because `TransferToGasKey` is callable by any predecessor account with no ownership check, an attacker can repeatedly top up a victim's gas key to keep its balance above `GasKeyInfo::MAX_BALANCE_TO_BURN`, causing every subsequent deletion attempt by the account owner to revert with `GasKeyBalanceTooHigh`, indefinitely.

### Finding Description
`check_actor_permissions` in `runtime/runtime/src/actions.rs:760-800` requires `actor_id == account_id` for `Action::DeleteKey`, `Action::WithdrawFromGasKey`, and `Action::DeleteAccount`, but explicitly allows `Action::TransferToGasKey` from *any* predecessor (grouped with `CreateAccount`/`Transfer` at line 793-796, which perform no actor check). [1](#0-0) 

`delete_gas_key` (invoked from `action_delete_key`) checks the gas key's balance and rejects deletion if it exceeds `GasKeyInfo::MAX_BALANCE_TO_BURN`: [2](#0-1) 

The same guard exists for the aggregate balance on `action_delete_account`: [3](#0-2) 

Since anyone can call `TransferToGasKey` against an arbitrary account/public key to top up its gas-key balance, an attacker can watch for (or front-run) the victim's `DeleteKey`/`DeleteAccount` transaction and inject a `TransferToGasKey` deposit that pushes the balance back above `MAX_BALANCE_TO_BURN` before the victim's transaction executes, causing the deletion to fail with `GasKeyBalanceTooHigh` — mirroring the reported Solidity pattern where any unprivileged party can transfer tokens into a pool being removed to force a revert of the removal.

### Impact Explanation
This blocks a legitimate account holder from ever deleting a gas key or account whose gas-key balance the attacker keeps re-inflating, freezing the account/key in a permanently un-deletable state (denial of a state transition the owner is entitled to perform). It does not directly cause fund loss for the attacker (attacker's own deposited tokens go to the gas key, which is recoverable via `WithdrawFromGasKey` first), but it can be used to grief accounts, keep storage/keys alive against the owner's wishes, and repeatedly force failed/wasted transactions from the victim (denial-of-service on a state-changing user transaction, satisfying the "transaction-triggered halt of the intended state transition" bar for the specific account).

### Likelihood Explanation
Moderate-to-low: it requires the attacker to notice or race a specific victim's deletion transaction and repeatedly re-fund the gas key with `TransferToGasKey`, which costs the attacker gas plus locked funds each time (funds are recoverable, so cost is essentially just gas + timing effort). No special privilege is needed — any RPC caller/transaction signer can execute `TransferToGasKey` against any target account.

### Recommendation
Restrict `TransferToGasKey` to be self-initiated (`actor_id == account_id`) or provide the owner an unconditional way to sweep/burn excess gas-key balance atomically as part of `DeleteKey`/`DeleteAccount` rather than reverting when the balance exceeds `MAX_BALANCE_TO_BURN`. Alternatively, cap or refund the excess back to the sender when a `TransferToGasKey` deposit would push the balance above the burn-safe threshold, so unprivileged deposits cannot be weaponized to block deletion.

### Proof of Concept
1. Victim's account `alice.near` has a gas key with public key `pk` and balance below `MAX_BALANCE_TO_BURN`.
2. Alice submits `DeleteKey(pk)` (or `DeleteAccount`) intending to delete/reclaim funds.
3. Attacker (any account) submits `TransferToGasKey { public_key: pk, deposit: X }` targeting `alice.near`'s gas key, where `X` is large enough to push the aggregate gas-key balance above `GasKeyInfo::MAX_BALANCE_TO_BURN`, and ensures it lands before or in the same block as Alice's deletion tx.
4. Alice's `DeleteKey`/`DeleteAccount` receipt hits the check in `delete_gas_key` / `action_delete_account` and fails with `ActionErrorKind::GasKeyBalanceTooHigh`, per `runtime/runtime/src/access_keys.rs:103-111` and `runtime/runtime/src/actions.rs:371-379`.
5. Attacker repeats step 3 whenever Alice retries, permanently preventing the deletion — analogous to front-running pool removal with a token transfer in the reported `YRizStrategy.sol` bug.

Note: I could not fully trace whether `WithdrawFromGasKey` (self-only) could be raced in the same block to neutralize this before `DeleteKey` executes in practice (ordering/atomicity across receipts within one transaction's action list would need runtime-level confirmation), which affects exact exploitability details but not the core permission-model gap identified.

### Citations

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

**File:** runtime/runtime/src/actions.rs (L760-800)
```rust
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
        Action::DeterministicStateInit(_) | Action::UniversalStateInit(_) => (),
    };
    Ok(())
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
