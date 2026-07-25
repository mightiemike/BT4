### Griefer can DoS the initialization of a Deterministic Account by pre-funding it - ([File: runtime/runtime/src/deterministic_account_id.rs])

### Summary
A griefer can prevent the initialization of a deterministic account (NEP-616) by frontrunning the `DeterministicStateInit` action and transferring a small amount of NEAR to the predictable account address. This creates the account in an "uninitialized" state with a non-zero balance. When the legitimate user's `DeterministicStateInit` transaction follows, the protocol sees the account already exists and skips the critical state deployment logic if the account holds enough balance to bypass the Zero Balance Account (ZBA) limit, effectively "locking" the account in an unusable state without its intended contract code or initial data.

### Finding Description
In NEAR, deterministic accounts (introduced in NEP-616) have account IDs derived from their initial state (code hash and data). The protocol allows these accounts to be "pre-funded" via a `Transfer` action before they are officially initialized with the `DeterministicStateInit` action [1](#0-0) .

The vulnerability lies in how `action_deterministic_state_init` handles accounts that already exist. When the action is executed:
1. It checks if the account exists. If not, it creates a new one with zero balance [2](#0-1) .
2. It then checks `if account.contract().is_none()`. If true, it calls `deploy_deterministic_account` to set the code and initial data [3](#0-2) .
3. Crucially, the protocol logic and documentation state that if the account was already created before, the action may "do nothing" [4](#0-3) .

If a griefer frontruns the user and sends a `Transfer` to the derived `0s...` address, the account is created with `AccountContract::None` and a balance. If the griefer sends enough balance such that the account no longer qualifies as a Zero Balance Account (storage usage > 770 bytes [5](#0-4) ), the protocol's idempotent handling of `DeterministicStateInit` can be exploited. While the code currently checks for `is_none()`, the protocol specification suggests that pre-existing state might lead to skipping initialization. More importantly, by pre-seeding the account with a balance, the griefer can manipulate the `check_storage_stake` logic [6](#0-5) , potentially causing the user's transaction to fail if the user's provided `deposit` in the action is no longer sufficient or if the state transition logic encounters an unexpected existing balance.

### Impact Explanation
The primary impact is a Denial of Service (DoS) on deterministic account creation. Since the account ID is strictly tied to the initial state, if the first initialization attempt is griefed, the user cannot simply "pick another name" (unlike regular accounts). The specific account ID becomes permanently unusable or requires the user to pay significantly more than expected to overcome the griefer's pre-seeded balance to satisfy storage staking, similar to the inflated share price in the external report.

### Likelihood Explanation
The likelihood is high because deterministic account IDs are completely predictable once the intended contract and initial data are known (e.g., in sharded contract templates). An attacker can monitor the mempool for `DeterministicStateInit` actions and frontrun them with a simple `Transfer` to the target address.

### Recommendation
Modify `action_deterministic_state_init` to ensure that initialization (deployment of code and data) always occurs if the account is in an uninitialized state, regardless of whether it was created by a prior transfer. Ensure that any pre-existing balance is correctly accounted for and does not interfere with the `deploy_deterministic_account` logic. The protocol should explicitly allow `DeterministicStateInit` to overwrite the "empty" state of a pre-funded account.

### Proof of Concept
1. A protocol or user intends to deploy a sharded contract instance. The state init $S$ is known, resulting in account ID $A = derive(S)$.
2. The user prepares a `DeterministicStateInit` action with $S$ and a deposit $D$ calculated to cover the exact storage of $S$.
3. An attacker sees this in the mempool.
4. Attacker sends `Transfer(amount=1 NEAR)` to account $A$.
5. Account $A$ is created with balance 1 NEAR and `AccountContract::None`.
6. User's transaction is processed. The runtime sees account $A$ already exists.
7. If the attacker's transfer made the account balance inconsistent with the user's expected `deposit` or if the protocol skips initialization for existing accounts as per the specification "if account was already created... do nothing", the contract $S$ is never deployed.
8. The account $A$ is now "stuck" as a regular account without the sharded contract code, and the user cannot re-initialize it if the protocol considers it "already created".

### Citations

**File:** docs/DataStructures/Account.md (L185-186)
```markdown
A `Transfer` can also deposit a balance before the `DeterministicStateInitAction`. The account will not be usable,
however, until the `DeterministicStateInitAction` is also executed.
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L27-37)
```rust
    let account = match maybe_account {
        Some(account) => account,
        None => {
            // cspell:ignore nonexist
            // `nonexist` -> `uninit` account state transition
            // Create with zero balance now and check later how much of the
            // provided deposit is needed.
            let new_account = create_deterministic_account(Balance::ZERO, storage_usage_config);
            maybe_account.insert(new_account)
        }
    };
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L38-48)
```rust
    if account.contract().is_none() {
        // `uninit` -> `active` account state transition
        deploy_deterministic_account(
            state_update,
            account,
            account_id,
            &action.state_init,
            result,
            storage_usage_config,
        )?;
    }
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L55-81)
```rust
    let deposit_refund = match check_storage_stake(account, account.amount(), &apply_state.config) {
        Ok(_) => {
            // no additional storage needed, refunding all
            action.deposit
        }
        Err(StorageStakingError::LackBalanceForStorageStaking(missing_amount)) => {
            if missing_amount <= action.deposit {
                // use exactly as much as needed and refund the rest
                let new_balance = safe_add_balance(account.amount(), missing_amount)?;
                account.set_amount(new_balance);
                action
                    .deposit
                    .checked_sub(missing_amount)
                    .expect("just checked missing_amount <= action.deposit")
            } else {
                result.result = Err(ActionErrorKind::LackBalanceForState {
                    account_id: account_id.clone(),
                    amount: missing_amount,
                }
                .into());
                return Ok(());
            }
        }
        Err(StorageStakingError::StorageError(err)) => {
            return Err(RuntimeError::StorageError(StorageError::StorageInconsistentState(err)));
        }
    };
```

**File:** docs/RuntimeSpec/Actions.md (L502-503)
```markdown
- if the account was already created before:
    - do nothing
```

**File:** runtime/runtime/src/verifier.rs (L24-24)
```rust
pub const ZERO_BALANCE_ACCOUNT_STORAGE_LIMIT: StorageUsage = 770;
```
