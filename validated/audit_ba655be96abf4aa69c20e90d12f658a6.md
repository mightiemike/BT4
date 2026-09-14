## Answer

The reported bug class — repeatedly abusing a refund path to re-obtain a benefit that should only be granted once per identity — maps to a documented, still-unmitigated fund-loss pattern in nearcore's meta‑transaction (relayer) flow for implicit-account creation, exactly as acknowledged in the project's own architecture docs and integration tests.

### Title
Relayer funds can be repeatedly drained via implicit-account initialize → self-delete-and-cash-in cycles in meta-transactions - (`docs/architecture/how/meta-tx.md`, `runtime/runtime/src/actions.rs`)

### Summary
A relayer that funds/initializes a user's NEAR-implicit account (a standard, expected relayer service for onboarding users without gas) transfers real NEAR to cover storage staking. Once initialized, the user controls a full-access key and can, unprompted by the relayer, submit `DeleteAccountAction` themselves, sending the account's full balance (including the storage-stake reserve) to a beneficiary of their choosing. The account is destroyed, the relayer's up-front funding is fully recovered by the attacker, and the user can request the relayer redo the "free" initialization for a new implicit account (or, if permitted by the relayer's policy, the same address again after some interval), repeating the cycle indefinitely. This is the direct on-chain analog of "purchase → refund → repurchase" abuse for infinite free value.

### Finding Description
Meta-transactions (NEP-366) let a relayer pay gas/deposit costs on behalf of a sender that has no funds, notably to bootstrap brand-new implicit accounts [1](#0-0) . The nearcore docs explicitly flag the resulting attack: the relayer initializes Alice's account, and instead of using it for the intended meta-transaction, "she deletes her account and cashes in the small token balance reserved for storage. If this attack is repeated, a significant amount of tokens could be stolen from the relayer." [2](#0-1) 

The mechanism that enables the "refund" side of the cycle is `action_delete_account`, which pays the account's *entire remaining balance* (which includes the storage-stake reserve funded by the relayer) to an attacker-chosen `beneficiary_id`, with no cool-down, no per-identity/one-time restriction, and no linkage back to who funded the account: [3](#0-2) 

The integration test suite independently documents this as a known, currently-unfixed workflow risk when a relayer creates an implicit account via a meta-transaction transfer: [4](#0-3) 

NEP-448 "zero balance accounts" (`ZERO_BALANCE_ACCOUNT_STORAGE_LIMIT = 770` bytes) reduces — but does not eliminate — the exposure: a `NearImplicit` account created via transfer derives a full-access key whose storage footprint plus the transferred value (commonly at least 1 NEAR for it to be usable) puts it above the zero-balance limit, so the relayer must fund real storage stake for it to be a normal, full-access-key-bearing account: [5](#0-4) [6](#0-5) 

Once initialized with a full-access key and non-zero balance, the user needs no further relayer cooperation: they can sign and submit an ordinary `DeleteAccountAction` transaction themselves (paid from the very funds the relayer provided), redirecting the entire storage-stake reserve to themselves, and then ask the relayer (or a different relayer) to "onboard" a fresh implicit account, repeating the loop.

### Impact Explanation
Every relayer operating the standard "initialize implicit accounts for gas-less users" onboarding flow described in nearcore's own meta-tx documentation is exposed to unauthorized, repeatable value extraction: a single unprivileged transaction signer can convert relayer-subsidized "account bootstrap" funding into their own balance an arbitrary number of times by cycling through fresh implicit accounts (or the same one, if the relayer re-funds it), draining the relayer's NEAR balance with no protocol-level cap. This is concrete unauthorized value movement from a third party (the relayer) to the attacker, directly reachable by a normal transaction signer with no elevated privileges — matching the accepted impact class (unauthorized value movement / fund loss to a service provider) from the external report's free-trial refund/repurchase pattern.

### Likelihood Explanation
Likelihood is high for any deployed relayer that implements the exact onboarding flow nearcore's own docs and tests describe (fund a fresh implicit account so it can be used in a meta-transaction). The attack requires only: (1) requesting the relayer initialize an implicit account, (2) signing and submitting a self-authored `DeleteAccountAction` transaction with the beneficiary set to an attacker-controlled account, and (3) repeating. No special access, timing races, or node cooperation is needed — it is a plain sequence of transactions any account holder can submit.

### Recommendation
- Relayers should not treat "initialize implicit account with tokens" as free/repeatable per identity; nearcore's own docs suggest removing storage-staking cost so there is no financial incentive to delete-and-cash-in, or moving nonce/authorization checks to the relayer's own access key so it never needs to pre-fund an unknown implicit account with spendable storage stake.
- At the protocol level, consider making the storage-stake portion of `action_delete_account`'s refund non-fungible to the deleting party when the account was populated purely via meta-transaction transfer, or clearly document (and encourage relayer implementations to enforce) rate limiting/attribution per public key/IP to prevent unbounded repeat onboarding of throwaway implicit accounts.
- This should be treated as an operational/application-layer risk inherent to the relayer trust model (similar to how the sponsor in the cited report characterized free-trial abuse), but nearcore should ensure this caveat is prominently surfaced in relayer integration guidance since the underlying runtime primitives (`action_delete_account`, implicit-account creation via transfer) provide no built-in mitigation.

### Proof of Concept
1. Relayer submits a meta-transaction `Transfer` to a brand-new NEAR-implicit `AccountId`, funding it with e.g. 1 NEAR to cover storage stake and derive a full-access key, as in `meta_tx_create_implicit_account` [7](#0-6) .
2. The now-initialized account (control by the attacker, who owns the implicit account's private key) signs its own `SignedTransaction` containing `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: <attacker_account> })`, without any further relayer involvement.
3. `action_delete_account` pays out `account_ref.amount()` in full to the attacker's beneficiary account [8](#0-7) , and the implicit account is destroyed.
4. The attacker requests the relayer (or another relayer trusting the same onboarding contract/service) to bootstrap a new implicit account, and repeats steps 1–3 indefinitely, extracting relayer-funded NEAR each cycle.

### Citations

**File:** docs/architecture/how/meta-tx.md (L127-159)
```markdown
## Limitation: Accounts must be initialized

Any transaction, including meta transactions, must use NONCEs to avoid replay
attacks. The NONCE must be chosen by Alice and compared to a NONCE stored on
chain. This NONCE is stored on the access key information that gets initialized
when creating an account.

Implicit accounts don't need to be initialized in order to receive NEAR tokens,
or even $FT. This means users could own $FT but no NONCE is stored on chain for
them. This is problematic because we want to enable this exact use case with
meta transactions, but we have no NONCE to create a meta transaction.

For the MVP, the proposed solution, or work-around, is that the relayer will
have to initialize the account of Alice once if it does not exist. Note that
this cannot be done as part of the meta transaction. Instead, it will be a
separate transaction that executes first. Only then can Alice even create a
`SignedDelegateAction` with a valid NONCE.

Once again, some trust is required. If Alice wanted to abuse the relayer's
helpful service, she could ask the relayer to initialize her account.
Afterwards, she does not sign a meta transaction, instead she deletes her
account and cashes in the small token balance reserved for storage. If this
attack is repeated, a significant amount of tokens could be stolen from the
relayer.

One partial solution suggested here was to remove the storage staking cost from
accounts. This means there is no financial incentive for Alice to delete her
account. But it does not solve the problem that the relayer has to pay for the
account creation and Alice can simply refuse to send a meta transaction
afterwards. In particular, anyone creating an account would have financial
incentive to let a relayer create it for them instead of paying out of their own
pockets. This would still be better than Alice stealing tokens but
fundamentally, there still needs to be some trust.
```

**File:** runtime/runtime/src/actions.rs (L380-387)
```rust
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
    let remove_result = remove_account(state_update, account_id)?;
```

**File:** integration-tests/src/tests/features/delegate_action.rs (L949-996)
```rust
/// Creating an implicit account with a meta tx transfer and try using the account in
/// a second meta transaction.
///
/// Creation through a meta tx should work as normal, it's just that the relayer
/// pays for the storage and the user could delete the account and cash in,
/// hence this workflow is not ideal from all circumstances.
///
/// Using the account should only work for NEAR-implicit accounts. The other implicit
/// kinds get no access key from the transfer, so they are reachable only through the
/// contract or state init behind them.
fn meta_tx_create_implicit_account(new_account: AccountId) {
    let relayer = bob_account();
    let sender = alice_account();
    let node = RuntimeNode::new(&relayer);

    // Check account doesn't exist, yet
    node.view_account(&new_account).expect_err("account already exists");

    let fee_helper = fee_helper(&node);
    let initial_amount = match new_account.get_account_type() {
        AccountType::NearImplicitAccount => Balance::from_near(1),
        // NEAR deterministic accounts fit within zero-balance account limit.
        AccountType::NearDeterministicAccount => Balance::ZERO,
        // ETH-implicit accounts fit within zero-balance account limit.
        AccountType::EthImplicitAccount => Balance::ZERO,
        // Universal accounts fit within zero-balance account limit.
        AccountType::UniversalAccount => Balance::ZERO,
        AccountType::NamedAccount => panic!("must be implicit"),
    };
    let actions = vec![Action::Transfer(TransferAction { deposit: initial_amount })];

    let tx_cost = match new_account.get_account_type() {
        AccountType::NearImplicitAccount => fee_helper.create_account_transfer_full_key_cost(),
        AccountType::NearDeterministicAccount => fee_helper.create_account_transfer_cost(),
        AccountType::EthImplicitAccount => fee_helper.create_account_transfer_cost(),
        AccountType::UniversalAccount => fee_helper.create_account_transfer_cost(),
        AccountType::NamedAccount => panic!("must be implicit"),
    };
    check_meta_tx_no_fn_call(
        &node,
        actions,
        tx_cost,
        initial_amount,
        sender.clone(),
        relayer.clone(),
        new_account.clone(),
    );

```

**File:** runtime/runtime/src/verifier.rs (L48-90)
```rust
pub fn check_storage_stake(
    account: &Account,
    account_balance: Balance,
    runtime_config: &RuntimeConfig,
) -> Result<(), StorageStakingError> {
    let billable_storage_bytes = account.storage_usage();
    let required_amount = runtime_config
        .storage_amount_per_byte()
        .checked_mul(u128::from(billable_storage_bytes))
        .ok_or_else(|| {
            format!(
                "Account's billable storage usage {} overflows multiplication",
                billable_storage_bytes
            )
        })
        .map_err(StorageStakingError::StorageError)?;
    let available_amount = account_balance
        .checked_add(account.locked())
        .ok_or_else(|| {
            format!(
                "Account's amount {} and locked {} overflow addition",
                account.amount(),
                account.locked(),
            )
        })
        .map_err(StorageStakingError::StorageError)?;
    if available_amount >= required_amount {
        Ok(())
    } else {
        if is_zero_balance_account(account) {
            return Ok(());
        }
        Err(StorageStakingError::LackBalanceForStorageStaking(
            required_amount.checked_sub(available_amount).unwrap(),
        ))
    }
}

/// Zero Balance Account introduced in NEP 448 https://github.com/near/NEPs/pull/448
/// An account is a zero balance account if and only if the account uses no more than `ZERO_BALANCE_ACCOUNT_STORAGE_LIMIT` bytes
fn is_zero_balance_account(account: &Account) -> bool {
    account.storage_usage() <= ZERO_BALANCE_ACCOUNT_STORAGE_LIMIT
}
```
