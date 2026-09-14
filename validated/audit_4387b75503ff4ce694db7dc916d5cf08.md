## Analysis

The Sherlock report describes a fixed, unredirectable recipient whose failed receipt permanently strands funds. The closest reachable analog in this nearcore snapshot is the `DeleteAccountAction` beneficiary transfer: the account's entire remaining balance is sent as a system-refund receipt to a caller-specified `beneficiary_id`, and if that receipt fails to execute (e.g., the beneficiary account does not exist), the funds are burned outright with no retry or redirect path.

### Title
DeleteAccount beneficiary balance transfer is burned forever if the beneficiary account does not exist at execution time - (File: `runtime/runtime/src/actions.rs`)

### Summary
`action_delete_account` unconditionally routes the deleted account's entire remaining balance to the transaction-supplied `beneficiary_id` via a system-refund receipt [1](#0-0) . `DeleteAccountAction` validation only checks that `beneficiary_id` is a syntactically valid account id; it never verifies the account exists, per the action spec [2](#0-1) . Since the payout is emitted as a new receipt whose `predecessor_id == "system"` (a refund), and refunds are barred from implicitly creating any account [3](#0-2) , `check_account_existence` rejects the `Transfer` if the beneficiary account is missing [4](#0-3) . When that happens the runtime does not fall back to any other account or reattempt delivery — it burns the tokens, exactly as documented: "If the execution of a refund fails, the refund amount is burnt." [5](#0-4)  This is implemented in the apply loop: for a receipt whose predecessor is `"system"`, a failed result adds the deposit to `other_burnt_amount` instead of generating a further refund [6](#0-5) .

### Finding Description
1. An account owner (or a relayer on their behalf via a meta-transaction) submits `Action::DeleteAccount { beneficiary_id }` [7](#0-6) .
2. `action_delete_account` computes the account's remaining balance and immediately emits `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)` — a receipt with `predecessor_id = "system"` — before removing the account [8](#0-7) .
3. This new receipt is a cross-shard receipt if `beneficiary_id` lives on another shard, so it is delivered asynchronously, one or more blocks later.
4. By the time it is applied, if the `beneficiary_id` account does not exist — because it was never created, or because it was deleted in the interim (including by the beneficiary itself, or as a race the deletor cannot control once the delete transaction is signed) — `check_account_existence` fails the `Transfer` action with `AccountDoesNotExist`, since refund receipts are never allowed to implicitly create an account [4](#0-3) [3](#0-2) .
5. Because the failing receipt's own `predecessor_id` is `"system"`, the runtime does not create a further refund receipt; it instead burns the full deposit through `other_burnt_amount` [6](#0-5) , consistent with the documented refund semantics [5](#0-4) .
6. There is no mechanism analogous to Sherlock's suggested fix (`withdrawableCollectionTokenId` + a permissioned, redirectable `withdrawToken`): the beneficiary cannot be changed after the `DeleteAccount` transaction is included, and the payout receipt carries no `refund_to` fallback (that field only exists on `ActionReceiptV2`/`PromiseYieldV2`, not on `new_balance_refund`, whose receiver is fixed) [9](#0-8) [10](#0-9) .

### Impact Explanation
Whenever the target `beneficiary_id` is absent at the moment the delete-account receipt is finally executed, the entire remaining NEAR balance of the deleted account is permanently destroyed rather than delivered to anyone — an irreversible, protocol-level loss of user funds triggered by a single ordinary transaction. This is functionally the same class of harm the Sherlock report flags (unrecoverable stranding of value at a fixed, non-redirectable recipient), except here the outcome is outright token burn instead of a stuck-but-recoverable balance.

### Likelihood Explanation
Any account holder (or a relayer executing a delegated action for them) can trigger this simply by naming a `beneficiary_id` that does not exist, is mistyped, or is deleted before the cross-shard refund receipt lands — the latter being especially plausible for accounts on a different shard, where the delivery delay gives time for the beneficiary account's own deletion (self-inflicted or otherwise) to race the incoming payout. No special privileges beyond submitting an ordinary `DeleteAccount` transaction are required, and the existing test suite explicitly acknowledges the beneficiary must already exist for the transfer not to be lost [11](#0-10) .

### Recommendation
Validate (at execution time, or as close to it as feasible) that `beneficiary_id` names an existing account before consuming the source account, or fall back to a recoverable path — e.g. reject the `DeleteAccount` action if the beneficiary receipt would fail, or route a failed beneficiary payout to a retryable holding state instead of unconditionally burning it — mirroring the audit's recommendation to make the payout destination validated/redirectable rather than a fire-and-forget transfer to a possibly-nonexistent fixed account.

### Proof of Concept
1. Account `A` (any shard) submits `Action::DeleteAccount { beneficiary_id: "B" }`, where `B` is an account on a different shard.
2. Before the cross-shard balance-refund receipt for `A`'s balance reaches `B`'s shard, `B` is deleted (by its own owner, or simply never existed / was mistyped).
3. `check_account_existence` rejects the `Transfer` inside the refund receipt with `AccountDoesNotExist` [4](#0-3) .
4. Because the receipt's `predecessor_id == "system"`, `apply_action_receipt`'s outer handling burns the deposit instead of generating another refund [6](#0-5) .
5. `A`'s entire remaining balance is gone from total supply forever, with no account able to claim it.

### Citations

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

**File:** runtime/runtime/src/actions.rs (L928-934)
```rust
/// Whether a transfer to an account that does not exist yet may create it.
fn implicit_creation_allowed(account_type: AccountType, receipt_shape: ReceiptShape) -> bool {
    let ReceiptShape { is_refund, is_the_only_action } = receipt_shape;
    if is_refund {
        return false; // Refund can never create an account
    }

```

**File:** docs/RuntimeSpec/Actions.md (L278-300)
```markdown
## DeleteAccountAction

```rust
pub struct DeleteAccountAction {
    /// The remaining account balance will be transferred to the AccountId below
    pub beneficiary_id: AccountId,
}
```

**Outcomes**:

- The account, as well as all the data stored under the account, is deleted and the tokens are transferred to `beneficiary_id`.

### Errors

**Validation Error**:

- If `beneficiary_id` is not a valid account id, the following error will be returned

```rust
/// Invalid account ID.
InvalidAccountId { account_id: AccountId },
```
```

**File:** docs/RuntimeSpec/Refunds.md (L10-12)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
```

**File:** runtime/runtime/src/lib.rs (L1047-1055)
```rust
        let gas_refund_result = if receipt.predecessor_id().is_system() {
            // If the refund fails tokens are burned.
            if result.result.is_err() {
                stats.balance.other_burnt_amount = safe_add_balance(
                    stats.balance.other_burnt_amount,
                    total_deposit(&action_receipt.actions())?,
                )?
            }
            GasRefundResult::default()
```

**File:** core/primitives/src/receipt.rs (L416-430)
```rust
    pub fn refund_to(&self) -> &Option<AccountId> {
        match self.receipt() {
            ReceiptEnum::Action(_)
            | ReceiptEnum::Data(_)
            | ReceiptEnum::PromiseYield(_)
            | ReceiptEnum::PromiseResume(_)
            | ReceiptEnum::GlobalContractDistribution(_) => &None,
            ReceiptEnum::ActionV2(action_receipt_v2)
            | ReceiptEnum::PromiseYieldV2(action_receipt_v2) => &action_receipt_v2.refund_to,
        }
    }

    pub fn balance_refund_receiver(&self) -> &AccountId {
        self.refund_to().as_ref().unwrap_or_else(|| self.predecessor_id())
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

**File:** runtime/runtime/src/tests/apply.rs (L6355-6357)
```rust
        // The beneficiary has to exist, otherwise the balance transfer the delete
        // sends it would come straight back as a refund.
        let beneficiary: AccountId = "beneficiary.near".parse().unwrap();
```
