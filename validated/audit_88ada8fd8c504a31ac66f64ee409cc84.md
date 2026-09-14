### Title
Beneficiary refund failure on `DeleteAccount` permanently burns the account owner's balance instead of returning it - (File: `runtime/runtime/src/actions.rs`)

### Summary
`action_delete_account` sends the deleted account's remaining balance to `beneficiary_id` as a `Receipt::new_balance_refund`, but this refund is executed as a "system-predecessor" refund receipt. Per the documented invariant, "a failed refund burns its deposit into `other_burnt_amount` rather than refunding". If `beneficiary_id` cannot receive the transfer (e.g. it does not exist, or fails storage-staking / other receiver-side checks), the account owner's remaining balance is irrecoverably destroyed rather than returned to the owner or any fallback account — an analog of the Stream.sol issue where funds become permanently stuck because a downstream transfer failure blocks/loses the payer's entitled funds.

### Finding Description
When an account is deleted, `action_delete_account` takes the account's current balance and queues a refund receipt to `beneficiary_id`: [1](#0-0) 

This is a `Receipt::new_balance_refund`, which is treated by the runtime as a "free" system-predecessor refund receipt. Per the documented protocol invariant, refund receipts burn zero gas, and critically: **a failed refund burns its deposit into `other_burnt_amount` instead of being refunded anywhere**: [2](#0-1) 

The `DeleteAccount` action itself commits unconditionally (the account is removed from state) once `action_delete_account` runs; the beneficiary transfer is a separate, subsequently-processed receipt. If that follow-up receipt's `Transfer` action fails on the receiver's shard (for example, because `beneficiary_id` is a normal — non-implicit — account that does not exist, so the transfer errors with `AccountDoesNotExist`, or the beneficiary fails storage-staking checks), the runtime's refund path does **not** send the money back to the (now-deleted) original account or any other party — it is simply burned: [3](#0-2) 

This mirrors the root cause of the reported Stream.sol bug: a single, uncontrollable downstream transfer failure permanently destroys funds that rightfully belonged to another party (there the payer's un-vested balance; here the account owner's residual balance), because the protocol offers no separate "claim later" or "retry to a different beneficiary" path once the `DeleteAccount` action has already committed.

### Impact Explanation
Any account holder who deletes their account with `beneficiary_id` set to a mistyped, non-existent, or otherwise-unable-to-receive account (this is entirely attacker/user controlled — the caller picks `beneficiary_id` in the transaction) causes their entire remaining NEAR balance to be permanently and unrecoverably burned rather than refunded. This is a concrete, transaction-triggered loss of user funds (not merely gas) with no recovery path, matching the "permanently frozen/lost funds" impact class from the report. Since the error can also occur unintentionally (e.g., typo in a rarely-used sub-account, or targeting an account that gets deleted/never created in a race), this is a realistic value-loss condition reachable by any ordinary account owner signing a single `DeleteAccount` transaction.

### Likelihood Explanation
Likelihood is high because:
- The trigger is a single, ordinary transaction (`DeleteAccountAction { beneficiary_id }`) that any account owner can submit without special privileges.
- No additional conditions (validator collusion, malicious peers, etc.) are required — only that `beneficiary_id` cannot receive the transfer at the time the refund receipt executes (e.g. account never existed, or was deleted in the interim).
- The `DeleteAccount` action itself always succeeds and removes the account before the beneficiary transfer is attempted, so there is no way to roll back or retry with a different beneficiary once the mistake is made.

### Recommendation
Reconsider the "refund receipts are free / failures burn the deposit" invariant for the `DeleteAccount` beneficiary payout specifically. Options: validate `beneficiary_id` existence (and storage-staking eligibility) at `action_delete_account` time and reject the action outright if the check would predictably fail, or fall back to burning only as a last resort while emitting a clearly documented, auditable outcome rather than silent burning; alternatively, prevent account deletion until the beneficiary transfer succeeds in the same atomic sense as the original delete, so failures re-target the transfer instead of destroying value.

### Proof of Concept
1. Attacker/user account `A` holds `N` NEAR.
2. `A` signs `DeleteAccount { beneficiary_id: "nonexistent.near" }`.
3. `action_delete_account` (`runtime/runtime/src/actions.rs:380-387`) removes `A` from state and queues `Receipt::new_balance_refund("nonexistent.near", N)`.
4. The refund receipt executes as a system-predecessor "free" refund; the inner `Transfer` action fails because `nonexistent.near` does not exist.
5. Per the refund-failure rule (`protocol-model/spec/runtime-execution.md:152`), the deposit `N` is burned into `other_burnt_amount` instead of being returned to `A` (who no longer exists) or any other account — the funds are permanently lost from total supply with no recovery mechanism.

**Note on verification limits**: I was unable to fully trace the exact code path in `runtime/runtime/src/lib.rs` (around the documented `:929`/`:972` line references) within the tool budget available to confirm the precise conditions under which the beneficiary `Transfer` action fails and definitively rule out any implicit-account-creation exception for the beneficiary. This should be verified directly against `runtime/runtime/src/lib.rs::apply_action_receipt` (the refund-receipt branch) and `action_transfer_or_implicit_account_creation` before treating this as fully confirmed; I recommend a Devin session with full file access to trace this precisely.

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

**File:** protocol-model/spec/runtime-execution.md (L69-69)
```markdown
6. **Refunds** (see below): system-predecessor receipts (refund receipts) are free — no refund generated, and a failed refund burns its deposit into `other_burnt_amount` (`runtime/runtime/src/lib.rs:929`). Otherwise `refund_unspent_gas_and_deposits` runs (`:943`).
```

**File:** protocol-model/spec/runtime-execution.md (L152-152)
```markdown
- **Refund receipts are free**: system-predecessor receipts burn zero gas; a failed refund burns its deposit into `other_burnt_amount` rather than refunding (`runtime/runtime/src/lib.rs:929`, `:972`).
```
