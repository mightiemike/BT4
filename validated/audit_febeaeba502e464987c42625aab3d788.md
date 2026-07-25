### Title
Third-party `TransferToGasKey` can DoS `DeleteKey`/`DeleteAccount` by pushing gas key balance above the burn threshold — (`runtime/runtime/src/access_keys.rs`)

### Summary

`delete_gas_key` and `action_delete_account` enforce a hard `> MAX_BALANCE_TO_BURN` (1 NEAR) check before allowing a gas key to be deleted. Because `TransferToGasKey` carries no restriction on who may call it, any unprivileged account can increase a victim's gas key balance above that threshold, causing the victim's `DeleteKey` or `DeleteAccount` transaction to fail with `GasKeyBalanceTooHigh`. This is the nearcore analog of M-13: an exact-amount check against a shared mutable resource that a third party can manipulate to cause the operation to revert.

---

### Finding Description

`delete_gas_key` enforces:

```rust
if gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN {
    result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh { ... });
    return Ok(());
}
``` [1](#0-0) 

`MAX_BALANCE_TO_BURN` is a hard constant of 1 NEAR: [2](#0-1) 

`action_delete_account` applies the same check on the aggregate of all gas key balances: [3](#0-2) 

`action_transfer_to_gas_key` — callable by any account, including via the contract host function `append_action_transfer_to_gas_key` — unconditionally adds `action.deposit` to the target gas key balance with no check on the caller's identity: [4](#0-3) 

The host function is exposed to contracts: [5](#0-4) 

`WithdrawFromGasKey`, by contrast, is explicitly restricted to transactions only (no host function), so only the account owner can reduce the balance: [6](#0-5) 

**Attack path:**

1. Victim holds a gas key with balance B ≤ 1 NEAR and submits `DeleteKey { public_key: gas_key }`.
2. Attacker observes the pending transaction and submits `receiver=victim, actions=[TransferToGasKey { public_key: gas_key, deposit: (1 NEAR − B + 1 yocto) }]`.
3. If the attacker's transaction lands first in the same block, the gas key balance becomes > 1 NEAR.
4. Victim's `DeleteKey` executes and hits the `GasKeyBalanceTooHigh` guard, reverting with no state change.
5. The attacker repeats this every time the victim attempts deletion, spending only the marginal top-up amount each round.

The same path applies to `DeleteAccount` via the aggregate balance check.

---

### Impact Explanation

The victim is permanently unable to delete their gas key or their account as long as the attacker keeps the gas key balance above 1 NEAR. Because `TransferToGasKey` can be automated through a contract, the attacker's cost per round is as low as 1 yoctoNEAR plus gas. The victim's only escape is to combine `WithdrawFromGasKey` and `DeleteKey` in a single atomic transaction (so the attacker cannot interleave), but this is non-obvious and undocumented. The broken invariant is: **an account owner must always be able to delete their own gas key when its balance is within the burnable threshold**, which is violated by an unprivileged third party.

Impact class: contract execution flow breakage / denial of service (non-network-level, fixable without a hardfork).

---

### Likelihood Explanation

- Any unprivileged account can send `TransferToGasKey` to any other account's gas key.
- The attack cost is minimal when the victim's gas key balance is already close to 1 NEAR.
- NEAR's transaction ordering within a block is not guaranteed, so front-running is feasible.
- A malicious contract can automate the top-up, making sustained griefing cheap.

---

### Recommendation

Apply the same fix philosophy as M-13 — remove the hard exact-amount revert and instead handle the excess gracefully. Concretely, one of:

1. **Restrict `TransferToGasKey` to the account owner**: add a check in `action_transfer_to_gas_key` that `predecessor_id == account_id` (i.e., only the account itself may fund its own gas key). This is the cleanest fix and matches the `WithdrawFromGasKey` restriction.

2. **Allow deletion even when balance > MAX_BALANCE_TO_BURN**: change `delete_gas_key` to burn up to `MAX_BALANCE_TO_BURN` and return the remainder to the account balance, removing the hard revert. This mirrors the M-13 mitigation of using `min(deficit, amount)`.

---

### Proof of Concept

```
// Setup: victim has gas key with balance = 0.999_999_999_999_999_999_999_999 NEAR (just under 1 NEAR)

// Step 1: victim submits DeleteKey
tx_victim = Transaction {
    signer_id: "victim.near",
    receiver_id: "victim.near",
    actions: [DeleteKey { public_key: gas_key_pk }],
    nonce: N,
    ...
}

// Step 2: attacker front-runs in the same block
tx_attacker = Transaction {
    signer_id: "attacker.near",
    receiver_id: "victim.near",
    actions: [TransferToGasKey { public_key: gas_key_pk, deposit: 2 }],  // 2 yoctoNEAR
    nonce: M,
    ...
}

// Result: gas key balance = 1 NEAR + 1 yocto > MAX_BALANCE_TO_BURN
// victim's DeleteKey fails: ActionErrorKind::GasKeyBalanceTooHigh { balance: 1_000_000_000_000_000_000_000_001 }
// attacker cost: 2 yoctoNEAR + gas
// victim's gas key is permanently undeletable as long as attacker repeats
```

Relevant code locations:

- Hard threshold check: [7](#0-6) 
- Unconstrained balance increase: [8](#0-7) 
- `DeleteAccount` aggregate check: [9](#0-8) 
- `GasKeyBalanceTooHigh` error definition: [10](#0-9) 
- `MAX_BALANCE_TO_BURN` constant: [2](#0-1)

### Citations

**File:** runtime/runtime/src/access_keys.rs (L103-111)
```rust
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

**File:** runtime/runtime/src/access_keys.rs (L257-287)
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
```

**File:** core/primitives-core/src/account.rs (L552-554)
```rust
    /// Maximum gas key balance that can be burned during key or account deletion.
    /// Deletion fails if the (sum of) gas key balance(s) exceeds this threshold.
    pub const MAX_BALANCE_TO_BURN: Balance = Balance::from_near(1);
```

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

**File:** runtime/near-vm-runner/src/logic/dependencies.rs (L500-516)
```rust
    /// Attach the [`TransferToGasKeyAction`] action to an existing receipt.
    ///
    /// # Arguments
    ///
    /// * `receipt_index` - an index of Receipt to append an action
    /// * `public_key` - the public key of the gas key to fund
    /// * `deposit` - amount of tokens to transfer to the gas key
    ///
    /// # Panics
    ///
    /// Panics if the `receipt_index` does not refer to a known receipt.
    fn append_action_transfer_to_gas_key(
        &mut self,
        receipt_index: ReceiptIndex,
        public_key: PublicKey,
        deposit: Balance,
    );
```

**File:** core/primitives/src/action/mod.rs (L311-314)
```rust
/// Withdraw NEAR from a gas key's balance to the account.
///
/// This action must only be available via transactions, not via contract execution
/// (there is no corresponding promise batch action host function).
```

**File:** core/primitives/src/errors.rs (L840-846)
```rust
    /// Gas key balance is too high to burn during deletion
    GasKeyBalanceTooHigh {
        account_id: AccountId,
        /// Set for DeleteKey (specific key), None for DeleteAccount (aggregate)
        public_key: Option<Box<PublicKey>>,
        balance: Balance,
    } = 25,
```
