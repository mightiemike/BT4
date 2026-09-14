Confirmed root cause. The exact mechanism is nailed down at `runtime/runtime/src/lib.rs:1047-1054`: `if receipt.predecessor_id().is_system() { if result.result.is_err() { stats.balance.other_burnt_amount = safe_add_balance(...) } }` — "If the refund fails tokens are burned." This applies to the balance-refund receipt that `action_delete_account` (`runtime/runtime/src/actions.rs:380-386`) unconditionally emits to `beneficiary_id`, which is never checked for existence at signing or execution time (only its string format is validated).

### Title
DeleteAccount permanently burns the account balance when `beneficiary_id` does not exist - (File: `runtime/runtime/src/actions.rs`)

### Summary
An unprivileged signer submitting a single `DeleteAccount` action with a syntactically-valid but non-existent (or merely mistyped) `beneficiary_id` causes the deleting account's entire remaining balance to be permanently destroyed (burnt), with no possibility of recovery, while the account itself is irreversibly removed from state in the same step. This mirrors the OpenSea incident's core impact class: an asset-moving action that targets an address the actor does not actually control/that does not exist results in permanent, protocol-level loss of value, with no atomicity tying the destructive step (account deletion) to the success of the value transfer.

### Finding Description
`action_delete_account` (`runtime/runtime/src/actions.rs:330-406`) deletes the account and, if it has a nonzero balance, pushes a `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)` (`actions.rs:382-386`) as a *new, separate* receipt. The account removal (`remove_account`, `actions.rs:387`) and `*account = None` (`actions.rs:404`) happen unconditionally and successfully in this same action — there is no dependency on whether the subsequently-created balance-refund receipt will actually succeed.

Crucially, `Receipt::new_balance_refund` (`core/primitives/src/receipt.rs:496-510`) sets `predecessor_id: "system"`. Per the refund semantics (`docs/RuntimeSpec/Refunds.md:10-12`, and enforced in code at `runtime/runtime/src/lib.rs:1047-1054`):
```
let gas_refund_result = if receipt.predecessor_id().is_system() {
    // If the refund fails tokens are burned.
    if result.result.is_err() {
        stats.balance.other_burnt_amount = safe_add_balance(..., total_deposit(&action_receipt.actions())?)?
    }
    GasRefundResult::default()
} ...
```
Any receipt whose predecessor is `"system"` — which includes both ordinary deposit refunds AND the delete-account beneficiary payout — is specially recognized as unrefundable-on-failure: if its lone `Transfer` action fails, the deposit is added straight to `other_burnt_amount` instead of being sent anywhere else.

The `Transfer` action to a non-existent `beneficiary_id` fails deterministically via `check_account_existence` (`actions.rs:842-849`), because `implicit_creation_allowed` (`actions.rs:928-947`) unconditionally returns `false` when `is_refund` is true (i.e., predecessor is `"system"`) — regardless of account type. So there is no path by which this receipt can succeed against a missing account; the failure is guaranteed to occur.

Docs for the action (`docs/RuntimeSpec/Actions.md:278-318`, "DeleteAccountAction") only document an `InvalidAccountId` validation error for a malformed `beneficiary_id`; there is no check anywhere (transaction validation, action validation, or execution) confirming the beneficiary account *exists* before the account is destroyed. A test comment (`runtime/runtime/src/tests/apply.rs:6355-6356`) even acknowledges the beneficiary "has to exist, otherwise the balance transfer the delete sends it would come straight back as a refund" — but per the code path traced above, a refund-of-a-refund is not resent anywhere; it is burnt.

### Impact Explanation
This is a concrete, protocol-level permanent loss of funds triggered by a single unprivileged transaction (self-signed `DeleteAccount`), analogous to the OpenSea case where a marketplace action irrevocably moved NFTs to an inaccessible/burn address. Unlike a plain `Transfer` to a non-existent account — which safely fails atomically and refunds the sender's deposit (see `test_refund_on_send_money_to_non_existent_account`, `integration-tests/src/tests/standard_cases/mod.rs:791-829`) — `DeleteAccount` is not atomic with its beneficiary payout: the account deletion always commits, and only the payout can fail, with failure meaning outright destruction of the balance rather than a bounce-back. Any tooling, wallet, contract, or relayer that constructs a `DeleteAccount` action with an unchecked/attacker- or user-supplied `beneficiary_id` (typo, deleted account, account that will not exist by execution time, or a maliciously supplied non-existent id in a contract-driven promise batch) destroys the victim's entire account balance with no recovery path.

### Likelihood Explanation
High reachability: any signer of an ordinary `DeleteAccount` transaction, or any contract issuing `promise_batch_action_delete_account` (`runtime/near-vm-runner/.../logic.rs:4038-4072`, reachable from any unprivileged `FunctionCall`), can trigger this by supplying a beneficiary id that is merely a typo or otherwise unallocated. No validator, network, or privileged capability is required — it is a single self-contained transaction/receipt sequence.

### Recommendation
Make the beneficiary balance transfer atomic with the account deletion: verify the beneficiary account exists (or reject/queue) before committing the deletion, or defer removing the account/state until the balance-refund receipt is confirmed to succeed. At minimum, do not treat the delete-account beneficiary payout as an unrefundable "system" refund receipt — route its failure back to the deleting account's `actor_id`/predecessor (as ordinary deposit refunds do) rather than into `other_burnt_amount`, or reject `DeleteAccount` actions whose `beneficiary_id` does not resolve to an existing account at execution time.

### Proof of Concept
1. Create account `victim.near` with a nonzero balance and no locked stake.
2. From `victim.near`, sign and submit a transaction containing a single `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "typo-does-not-exist.near".parse().unwrap() })` (a syntactically valid but never-created account id).
3. `action_delete_account` (`runtime/runtime/src/actions.rs:330-406`) executes successfully: `victim.near` is removed from state (`remove_account`, `*account = None`), and a `Receipt::new_balance_refund(&"typo-does-not-exist.near", account_balance)` is emitted as a new receipt.
4. When that receipt executes, `check_account_existence` fails with `AccountDoesNotExist` (`actions.rs:842-849`, since `implicit_creation_allowed` returns `false` for `is_refund == true`).
5. Because the failing receipt's predecessor is `"system"`, `runtime/runtime/src/lib.rs:1047-1054` adds `total_deposit` (the victim's entire former balance) to `stats.balance.other_burnt_amount` instead of refunding it anywhere.
6. Net effect: `victim.near`'s account and balance are both permanently gone; total supply is reduced by that amount with no recipient — reproducible deterministically by any single unprivileged transaction. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** runtime/runtime/src/actions.rs (L380-386)
```rust
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
```

**File:** runtime/runtime/src/actions.rs (L842-849)
```rust
        Action::Transfer(_) => {
            let account_type = get_account_type(account_id, config);
            if account.is_none() && !implicit_creation_allowed(account_type, receipt_shape) {
                return Err(ActionErrorKind::AccountDoesNotExist {
                    account_id: account_id.clone(),
                }
                .into());
            }
```

**File:** runtime/runtime/src/actions.rs (L928-947)
```rust
/// Whether a transfer to an account that does not exist yet may create it.
fn implicit_creation_allowed(account_type: AccountType, receipt_shape: ReceiptShape) -> bool {
    let ReceiptShape { is_refund, is_the_only_action } = receipt_shape;
    if is_refund {
        return false; // Refund can never create an account
    }

    match account_type {
        // Named accounts can never be implicitly created by transfer
        AccountType::NamedAccount => false,
        // Near-implicit, Eth-implicit, and deterministic accounts can only be created
        // if transfer is the only action, to avoid account hijacking.
        AccountType::NearImplicitAccount
        | AccountType::EthImplicitAccount
        | AccountType::NearDeterministicAccount => is_the_only_action,
        // Universal account creation does NOT require transfer to be the only action.
        // It cannot be hijacked by other actions batched with the transfer.
        AccountType::UniversalAccount => true,
    }
}
```

**File:** core/primitives/src/receipt.rs (L496-510)
```rust
    pub fn new_balance_refund(receiver_id: &AccountId, refund: Balance) -> Self {
        Receipt::V0(ReceiptV0 {
            predecessor_id: "system".parse().unwrap(),
            receiver_id: receiver_id.clone(),
            receipt_id: CryptoHash::default(),
            receipt: ReceiptEnum::Action(ActionReceipt {
                signer_id: "system".parse().unwrap(),
                signer_public_key: PublicKey::empty(KeyType::ED25519),
                gas_price: Balance::ZERO,
                output_data_receivers: vec![],
                input_data_ids: vec![],
                actions: vec![Action::Transfer(TransferAction { deposit: refund })],
            }),
        })
    }
```

**File:** runtime/runtime/src/lib.rs (L1047-1054)
```rust
        let gas_refund_result = if receipt.predecessor_id().is_system() {
            // If the refund fails tokens are burned.
            if result.result.is_err() {
                stats.balance.other_burnt_amount = safe_add_balance(
                    stats.balance.other_burnt_amount,
                    total_deposit(&action_receipt.actions())?,
                )?
            }
```

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```

**File:** runtime/runtime/src/tests/apply.rs (L6355-6357)
```rust
        // The beneficiary has to exist, otherwise the balance transfer the delete
        // sends it would come straight back as a refund.
        let beneficiary: AccountId = "beneficiary.near".parse().unwrap();
```
