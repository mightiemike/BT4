## Title
Permanent denial-of-deletion via unsolicited `TransferToGasKey` griefing of an account's gas key ("`GasKeyBalanceTooHigh`" lock) - (File: `runtime/runtime/src/access_keys.rs`)

### Summary
`DeleteKey`/`DeleteAccount` on a gas key refuses to proceed if the gas key's balance exceeds `GasKeyInfo::MAX_BALANCE_TO_BURN` (1 NEAR), returning `GasKeyBalanceTooHigh`. However, `TransferToGasKey` is a generic action that any predecessor account (anyone, unprivileged) can execute against an arbitrary target account's already-existing gas key, exactly like an ordinary `Transfer`, but here it inflates a balance that is later checked as a *precondition to delete*. This is the same bug class as the Sandclock report: an attacker can push a balance that gates a state-changing operation, purely by "sending funds" to a victim-controlled resource they do not own.

### Finding Description
`action_delete_key` routes to `delete_gas_key` when the key being removed is a gas key [1](#0-0) . `delete_gas_key` rejects the deletion outright if the stored balance is above the burn cap: [2](#0-1) 

`action_delete_account` performs the analogous aggregate check over all of an account's gas keys before allowing self-deletion of the whole account: [3](#0-2) 

The balance that trips this check is fed purely by `TransferToGasKey`, which only requires that the *target* account already has a gas key with the given public key — it performs **no check that the predecessor/signer is the account owner**, and simply increments the balance: [4](#0-3) 

Just like a plain `Transfer` action, `TransferToGasKey` can be included in a transaction whose `receiver_id` is any account (not just the signer's own account), and is dispatched the same way in the action-application loop with no special actor-permission gate visible at the action-execution site: [5](#0-4) 

Gas key public keys are not secret — they are stored as ordinary access keys in state and are publicly queryable via view calls / RPC and visible in any prior `AddKey`/`TransferToGasKey` transaction, so an attacker can trivially learn the public key of a victim's gas key and target it.

### Impact Explanation
This maps to the report's "changing a strategy can be bricked" pattern: a threshold-based precondition on a resource controlled by the victim can be poisoned by depositing funds that anyone is permitted to send. Concretely:
- An attacker repeatedly calls `TransferToGasKey` against a victim's gas key to push its balance above `MAX_BALANCE_TO_BURN` (1 NEAR).
- Every subsequent `DeleteKey` for that gas key, or `DeleteAccount` for the whole account (which sums all gas-key balances against the same threshold), fails with `GasKeyBalanceTooHigh`.
- This can permanently deny the account owner the ability to delete a compromised/unwanted gas key or to delete their account and reclaim the remaining NEAR balance to a beneficiary — a "permanently frozen funds"/DoS outcome directly analogous to the cited report ("griefers gonna grief").

### Likelihood Explanation
Partially mitigated but not fully closed: `WithdrawFromGasKey` exists as the "escape hatch" allowing the owner to move balance out of the gas key before deleting it (analogous to redeeming aUST in the original report) [6](#0-5) , so the vulnerability's severity/likelihood hinges on whether the owner can atomically withdraw-then-delete in one transaction to avoid a race against a re-griefing `TransferToGasKey`. I was not able to fully verify (due to running out of tool iterations) whether `WithdrawFromGasKey` and `DeleteKey`/`DeleteAccount` can be combined in a single atomic transaction against the same gas key, whether `action_validation.rs` imposes any ordering/action-combination restriction preventing this, and whether `check_actor_permissions` in `verifier.rs` restricts who is allowed to submit a `TransferToGasKey` targeting another account's receiver_id. These are the concrete open questions that determine whether this is a permanent brick (matching "Medium/High, permanently frozen funds") versus a repeatable-but-recoverable nuisance.

### Recommendation
- Exclude gas-key balance funded via third-party `TransferToGasKey` from counting toward `MAX_BALANCE_TO_BURN` unless the depositor is the account owner (predecessor == account_id), or
- Allow `WithdrawFromGasKey` to be chained atomically with `DeleteKey`/`DeleteAccount` in the same transaction/receipt so the owner can always self-heal within a single atomic execution unaffected by interleaving griefing transactions, and/or
- Cap `TransferToGasKey` deposits from non-owners, or require gas-key funding to only be permitted by the account itself (self-fund via promise batch, as already exercised in `test_gas_key_transfer_host_function`) rather than via a generic top-level `Action::TransferToGasKey` reachable by any predecessor.

### Proof of Concept
1. Victim account `victim.near` adds a gas key `pk_gas` via `AddKey` with `AccessKey::gas_key_full_access(..)`.
2. Attacker (any unprivileged account `attacker.near`) submits a transaction with `signer_id = attacker.near`, `receiver_id = victim.near`, `actions = [TransferToGasKey { public_key: pk_gas, deposit: 2 NEAR }]`. This succeeds per `action_transfer_to_gas_key` [4](#0-3) , since there is no ownership check, mirroring `test_gas_key_transaction`'s use of `TransferToGasKeyAction` from a transaction targeting the gas-key owner [7](#0-6) , except here the sender is not the account owner.
3. Victim now attempts `DeleteKey(pk_gas)` or `DeleteAccount`. Both fail with `GasKeyBalanceTooHigh` because `gas_key_info.balance (2 NEAR) > GasKeyInfo::MAX_BALANCE_TO_BURN (1 NEAR)` [2](#0-1)  and [3](#0-2) .
4. Attacker can repeat step 2 any time the victim reduces the balance via `WithdrawFromGasKey`, re-inflating it before the victim's next `DeleteKey`/`DeleteAccount` transaction is included, absent an atomic withdraw+delete guarantee.

### Citations

**File:** runtime/runtime/src/access_keys.rs (L60-72)
```rust
    let access_key = get_access_key(state_update, account_id, &delete_key.public_key)?;
    if let Some(access_key) = access_key {
        if let Some(gas_key_info) = access_key.gas_key_info() {
            delete_gas_key(
                config,
                state_update,
                account,
                result,
                account_id,
                &delete_key.public_key,
                &access_key,
                gas_key_info,
            )?;
```

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

**File:** runtime/runtime/src/lib.rs (L803-811)
```rust
            Action::TransferToGasKey(transfer_to_gas_key) => {
                metrics::ACTION_CALLED_COUNT.transfer_to_gas_key.inc();
                action_transfer_to_gas_key(
                    state_update,
                    &mut result,
                    account_id,
                    transfer_to_gas_key,
                )?;
            }
```

**File:** core/primitives/src/action/mod.rs (L337-352)
```rust
/// Withdraw NEAR from a gas key's balance to the account.
///
/// This action must only be available via transactions, not via contract execution
/// (there is no corresponding promise batch action host function).
#[derive(
    BorshSerialize,
    BorshDeserialize,
    PartialEq,
    Eq,
    Clone,
    Debug,
    serde::Serialize,
    serde::Deserialize,
    ProtocolSchema,
)]
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
```

**File:** test-loop-tests/src/tests/gas_keys.rs (L121-136)
```rust
    // Fund the gas key
    let gas_key_fund_amount = Balance::from_millinear(100);
    let block_hash = get_shared_block_hash(&env.node_datas, &env.test_loop.data);
    let fund_tx = SignedTransaction::from_actions(
        2, // nonce
        sender.clone(),
        sender.clone(),
        &create_user_test_signer(sender),
        vec![Action::TransferToGasKey(Box::new(TransferToGasKeyAction {
            public_key: gas_key_signer.public_key(),
            deposit: gas_key_fund_amount,
        }))],
        block_hash,
    );
    env.rpc_runner().run_tx(fund_tx, Duration::seconds(5));
    env.rpc_runner().run_for_number_of_blocks(1);
```
