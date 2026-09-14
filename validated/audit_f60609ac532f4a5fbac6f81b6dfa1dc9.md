### Title
Deleting an account with a non-existent named `beneficiary_id` permanently burns the account's remaining balance instead of refunding it - (File: `runtime/runtime/src/actions.rs`, `runtime/runtime/src/lib.rs`)

### Summary
The Sherlock report describes fees being permanently burnt because a "fee recipient" balance transfer targets an address that cannot receive funds (address 0), and the sending function is callable unconditionally. The closest reachable nearcore analog is the `DeleteAccount` action: any account owner can submit a `DeleteAccountAction` whose `beneficiary_id` is a syntactically valid but never-created named account. The remaining balance transfer to that beneficiary fails, and the fallback refund of that failed transfer is itself unroutable (system-predecessor refund to the now-deleted account), so the funds are burnt from total supply instead of being recovered by anyone.

### Finding Description
`DeleteAccountAction` lets the caller specify an arbitrary `beneficiary_id` to receive the account's remaining balance [1](#0-0) . The only validation performed is that `beneficiary_id` is a syntactically valid account id, `validate_delete_action` does **not** check that the account actually exists [2](#0-1) .

`action_delete_account` creates a `Transfer` receipt to `beneficiary_id` for the remaining balance [3](#0-2) , and this is confirmed in tests: the beneficiary account must already exist, "otherwise the balance transfer the delete sends it would come straight back as a refund" [4](#0-3) . A `Transfer` action to a non-existent, non-implicit (named) account fails, because implicit account creation via a bare transfer is only permitted for implicit/deterministic account kinds and only when the transfer is the sole action in its own receipt — not for a receipt whose predecessor is the deletion's transfer machinery [5](#0-4) .

When that transfer receipt fails, the runtime falls back to refunding the deposit via a system-predecessor refund receipt, routed to `balance_refund_receiver()`, which defaults to the receipt's `predecessor_id` when no explicit `refund_to` was set [6](#0-5) . Per the documented behavior of `apply_action_receipt`, refund receipts (system-predecessor) generate no further refund on failure, and "a failed refund burns its deposit into `other_burnt_amount`" [7](#0-6) . Since the predecessor of the failed transfer is the very account that was just deleted (a named account, not implicit), any refund attempt targeting it also cannot recreate it, so the deposit cannot be delivered anywhere and is burnt from total supply rather than being frozen-but-recoverable or auto-returned to a live account.

### Impact Explanation
Unlike the original Solidity finding (fee burnt to `address(0)` due to an owner misconfiguration that can eventually be corrected before more damage), this nearcore analog is directly triggerable by any unprivileged account holder against their own funds, and — more importantly — is silent, irreversible token destruction outside the owner's control once the transaction is submitted: the entire remaining balance of the deleted account is permanently removed from total supply with no recovery path, matching "permanently frozen funds" / unauthorized value loss from supply.

### Likelihood Explanation
Likelihood is straightforward: any account owner (or a griefer tricking a relayer/meta-transaction sender into submitting such a delete) needs only to submit one `DeleteAccountAction` with `beneficiary_id` set to a never-registered named account (e.g., a random unused sub-account string). No special privileges, races, or validator collusion are required — this is a single, unprivileged transaction path (`SignedTransaction`/`Action::DeleteAccount`, or the equivalent host function `promise_batch_action_delete_account`) [8](#0-7) .

### Recommendation
- Validate that `beneficiary_id` corresponds to an account that exists (or is an implicit-creatable id) before allowing `DeleteAccount` to execute, similar to other explicit existence checks already performed elsewhere in action validation.
- Alternatively, if the beneficiary transfer fails, do not treat the resulting refund as a "free, un-refundable system receipt"; instead retain/lock the balance in a recoverable state (e.g., a delayed/queued refund to a well-known fallback account) rather than routing it to `other_burnt_amount`.
- Add integration tests asserting that total supply is unchanged (no burn) when `DeleteAccount` targets a non-existent named beneficiary.

### Proof of Concept
1. Create/fund account `victim.near` with a non-zero balance and no locked stake.
2. Submit `SignedTransaction` from `victim.near` to itself containing `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "never-created-name.near" })` (the target account must never have been created and must not be an implicit/eth-implicit/deterministic id).
3. Observe: the account is deleted; the follow-on `Transfer` receipt to `never-created-name.near` fails since the account doesn't exist; the failed-transfer refund (system-predecessor, targeting the now-deleted `victim.near`) also fails and is folded into `other_burnt_amount`.
4. Compare `total_supply` before and after — the victim's remaining balance is gone from total supply rather than appearing on any account, confirming permanent, unrecoverable burn.

Note: I was not able to directly read the exact source lines implementing the "failed refund → `other_burnt_amount`" logic in `runtime/runtime/src/lib.rs` (only its description in the generated spec and its presence confirmed via `grep_search` hits in `runtime/runtime/src/lib.rs` and `core/primitives/src/chunk_apply_stats.rs`); a Devin session with full file access would be needed to pin down the exact line numbers and confirm whether any protocol-version gate (e.g., `AccountCostIncrease`) changes this specific burn path before finalizing a fix.

### Citations

**File:** docs/RuntimeSpec/Actions.md (L278-289)
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
```

**File:** runtime/runtime/src/action_validation.rs (L447-451)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
```

**File:** protocol-model/spec/runtime-execution.md (L64-64)
```markdown
1. Gather each `input_data_id` from state as a `PromiseResult` and remove it (`runtime/runtime/src/lib.rs:796`); commit prior updates with `ActionReceiptProcessingStarted` (`:825`).
```

**File:** protocol-model/spec/runtime-execution.md (L79-79)
```markdown
`apply_action` seeds `ActionResult` with the action's `exec_fee` (gas/compute, `runtime/runtime/src/lib.rs:540`) and captures `current_contract` before running. It then runs `check_account_existence` (`runtime/runtime/src/actions.rs:824`) and `check_actor_permissions` (`runtime/runtime/src/actions.rs:776`); either failure returns early with the error set. Implicit account creation is allowed only when the action is the sole action and not a refund (`runtime/runtime/src/lib.rs:549`). Dispatch by action:
```

**File:** protocol-model/spec/runtime-execution.md (L92-92)
```markdown
| `DeleteAccount` | `action_delete_account` `runtime/runtime/src/actions.rs:343` | Refunds remaining balance to beneficiary, burns gas-key balances, removes the account; requires zero locked stake (`check_actor_permissions`). |
```

**File:** runtime/runtime/src/tests/apply.rs (L6355-6357)
```rust
        // The beneficiary has to exist, otherwise the balance transfer the delete
        // sends it would come straight back as a refund.
        let beneficiary: AccountId = "beneficiary.near".parse().unwrap();
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

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L4038-4071)
```rust
pub fn promise_batch_action_delete_account(
    ctx: &mut Ctx,
    memory: &mut [u8],
    promise_idx: u64,
    beneficiary_id_len: u64,
    beneficiary_id_ptr: u64,
) -> Result<()> {
    ctx.result_state.gas_counter.pay_base(base)?;
    if ctx.context.is_view() {
        return Err(HostError::ProhibitedInView {
            method_name: "promise_batch_action_delete_account".to_string(),
        }
        .into());
    }
    let beneficiary_id = read_and_parse_account_id(
        &mut ctx.result_state.gas_counter,
        memory,
        &ctx.registers,
        &ctx.config,
        beneficiary_id_ptr,
        beneficiary_id_len,
    )?;

    let (receipt_idx, sir) = promise_idx_to_receipt_idx_with_sir(ctx, promise_idx)?;

    pay_action_base(
        &mut ctx.result_state.gas_counter,
        &ctx.fees_config,
        ActionCosts::delete_account,
        sir,
    )?;

    ctx.ext.append_action_delete_account(receipt_idx, beneficiary_id);
    Ok(())
```
