### Title
`DeleteAccount` with `beneficiary_id == account_id` permanently burns the entire account balance — (`File: runtime/runtime/src/actions.rs`)

---

### Summary

When a user submits a `DeleteAccount` action with `beneficiary_id` equal to the account being deleted, the runtime deletes the account first and then dispatches a balance-refund receipt addressed to the now-deleted account. Because that receipt is a system refund (`predecessor_id = "system"`), its failure causes the entire account balance to be permanently burnt rather than returned to anyone.

---

### Finding Description

`action_delete_account` in `runtime/runtime/src/actions.rs` executes in this order:

1. Reads `account_balance = account_ref.amount()` [1](#0-0) 
2. Pushes `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)` into `result.new_receipts` [2](#0-1) 
3. Calls `remove_account(state_update, account_id)` which erases the account from the trie [3](#0-2) 
4. Sets `*account = None` [4](#0-3) 

`Receipt::new_balance_refund` always sets `predecessor_id = "system"` and `signer_id = "system"`. [5](#0-4) 

When the outgoing receipt is later applied, `apply_action` computes:

```rust
let is_refund = receipt.predecessor_id().is_system();          // true
let implicit_account_creation_eligible = is_the_only_action && !is_refund;  // false
``` [6](#0-5) 

`check_account_existence` is then called with `implicit_account_creation_eligible = false`. For a `Transfer` action on a non-existent named account with that flag false, `check_transfer_to_nonexisting_account` returns `AccountDoesNotExist`. [7](#0-6) 

The comment in that function even acknowledges the scenario: *"Account deletion with beneficiary creates a refund, so it'll not create a new account."* [8](#0-7) 

Because the receipt is a system refund, its failure causes the deposit to be burnt rather than re-refunded (documented in `docs/RuntimeSpec/Refunds.md`: *"If the execution of a refund fails, the refund amount is burnt."*). [9](#0-8) 

No validation layer check prevents `beneficiary_id == account_id`. The existing test `test_validate_action_valid_delete_account` even passes a `DeleteAccountAction { beneficiary_id: alice_account() }` against `alice.near` and asserts it is valid, confirming the absence of any guard. [10](#0-9) 

---

### Impact Explanation

**Loss of funds.** Any account owner who calls `DeleteAccount { beneficiary_id: <own account id> }` loses their entire liquid balance permanently. The balance is neither transferred to a third party nor refunded; it is burnt by the protocol. Gas fees are also paid normally, so the attacker (or mistaken user) suffers a net loss equal to the full account balance.

---

### Likelihood Explanation

This requires only a single, valid, self-signed transaction from the account owner. No privileged role, no validator access, and no external contract is needed. The action passes all existing validation checks. A user error (copy-pasting their own account ID as the beneficiary) or a malicious relayer constructing a `DelegateAction` with `beneficiary_id = sender_id` can trigger it. Likelihood is low-to-medium for accidental occurrence but trivially exploitable as a self-harm vector.

---

### Recommendation

Add a guard at the start of `action_delete_account` (or in `validate_action` for `DeleteAccount`) that rejects the action when `beneficiary_id == account_id`:

```rust
// In action_delete_account, before any state mutation:
if &delete_account.beneficiary_id == account_id {
    result.result = Err(ActionErrorKind::DeleteAccountSelfBeneficiary {
        account_id: account_id.clone(),
    }.into());
    return Ok(());
}
```

Alternatively, add the check in `validate_action` so it is rejected at transaction-validation time, before any receipt is created.

---

### Proof of Concept

```
1. Alice has account "alice.near" with balance B > 0.
2. Alice submits a transaction:
     signer_id:   "alice.near"
     receiver_id: "alice.near"
     actions:     [DeleteAccount { beneficiary_id: "alice.near" }]
3. Runtime executes action_delete_account:
     - Reads account_balance = B
     - Pushes Receipt::new_balance_refund("alice.near", B)   // predecessor = "system"
     - Calls remove_account("alice.near")                    // account deleted from trie
4. The outgoing balance-refund receipt is routed to "alice.near".
5. apply_action sets is_refund = true, implicit_account_creation_eligible = false.
6. check_account_existence returns AccountDoesNotExist for "alice.near".
7. Receipt fails; since predecessor_id == "system", the deposit B is burnt.
8. Net result: Alice's balance B is permanently destroyed.
     alice.near no longer exists; B is gone from total supply.
```

### Citations

**File:** runtime/runtime/src/actions.rs (L350-355)
```rust
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
```

**File:** runtime/runtime/src/actions.rs (L356-356)
```rust
    let remove_result = remove_account(state_update, account_id)?;
```

**File:** runtime/runtime/src/actions.rs (L373-373)
```rust
    *account = None;
```

**File:** runtime/runtime/src/actions.rs (L829-848)
```rust
fn check_transfer_to_nonexisting_account(
    config: &RuntimeConfig,
    account_id: &AccountId,
    implicit_account_creation_eligible: bool,
) -> Result<(), ActionError> {
    if implicit_account_creation_eligible
        && account_is_implicit(account_id, config.wasm_config.eth_implicit_accounts)
    {
        // OK. It's implicit account creation.
        // Notes:
        // - Transfer action has to be the only action in the transaction to avoid
        // abuse by hijacking this account with other public keys or contracts.
        // - Refunds don't automatically create accounts, because refunds are free and
        // we don't want some type of abuse.
        // - Account deletion with beneficiary creates a refund, so it'll not create a
        // new account.
        Ok(())
    } else {
        Err(ActionErrorKind::AccountDoesNotExist { account_id: account_id.clone() }.into())
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

**File:** runtime/runtime/src/lib.rs (L547-549)
```rust
        let is_refund = receipt.predecessor_id().is_system();
        let is_the_only_action = actions.len() == 1;
        let implicit_account_creation_eligible = is_the_only_action && !is_refund;
```

**File:** docs/RuntimeSpec/Refunds.md (L12-12)
```markdown
If the execution of a refund fails, the refund amount is burnt.
```

**File:** runtime/runtime/src/action_validation.rs (L1026-1033)
```rust
    fn test_validate_action_valid_delete_account() {
        validate_action(
            &test_limit_config(),
            &Action::DeleteAccount(DeleteAccountAction { beneficiary_id: alice_account() }),
            &"alice.near".parse().unwrap(),
            PROTOCOL_VERSION,
        )
        .expect("valid action");
```
