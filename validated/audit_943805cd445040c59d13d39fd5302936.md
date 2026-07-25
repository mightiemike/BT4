### Title
Deposit Refund Misdirected to Sender Instead of Relayer in Meta-Transaction Execution — (`File: runtime/runtime/src/actions.rs`)

### Summary

In `apply_delegate_action`, the inner receipt spawned from a `DelegateAction` is constructed with `predecessor_id = sender_id` (Alice, the user). When that inner receipt fails on the receiver's shard, the deposit refund is sent to `predecessor_id` — Alice — not to the relayer who actually paid the deposit. An unprivileged user can exploit this by crafting a meta-transaction with a large attached deposit that is guaranteed to fail, causing the relayer to lose the deposit to the user.

### Finding Description

`apply_delegate_action` in `runtime/runtime/src/actions.rs` constructs the inner receipt as follows:

```rust
let new_receipt = Receipt::V0(ReceiptV0 {
    predecessor_id: sender_id.clone(),   // Alice — the DelegateAction sender
    receiver_id: delegate_action.receiver_id().clone(),
    ...
    receipt: ReceiptEnum::Action(ActionReceipt {
        signer_id: action_receipt.signer_id().clone(), // Relayer
        ...
    }),
});
``` [1](#0-0) 

The deposit for the inner actions is paid by the relayer at transaction-processing time (deducted from the relayer's account balance during `verify_and_charge_transaction`). However, the inner receipt's `predecessor_id` is set to `sender_id` (Alice), not the relayer.

When the inner receipt fails on the receiver's shard, `refund_unspent_gas_and_deposits` issues a deposit refund to `receipt.balance_refund_receiver()`:

```rust
if deposit_refund > Balance::ZERO {
    result.new_receipts.push(Receipt::new_balance_refund(
        receipt.balance_refund_receiver(),
        deposit_refund,
    ));
}
``` [2](#0-1) 

`balance_refund_receiver()` returns `refund_to` if set, otherwise `predecessor_id`:

```rust
pub fn balance_refund_receiver(&self) -> &AccountId {
    self.refund_to().as_ref().unwrap_or_else(|| self.predecessor_id())
}
``` [3](#0-2) 

Since the inner receipt is a `Receipt::V0` with `ReceiptEnum::Action` (not `ActionV2`), `refund_to()` always returns `&None`:

```rust
ReceiptEnum::Action(_) | ... => &None,
ReceiptEnum::ActionV2(action_receipt_v2) => &action_receipt_v2.refund_to,
``` [4](#0-3) 

Therefore `balance_refund_receiver()` resolves to `predecessor_id = sender_id = Alice`. The deposit refund is sent to Alice, not to the relayer who paid it.

The nearcore documentation explicitly acknowledges this:

> "But what if the transaction fails execution on Bob's shard? At this point, the predecessor is Alice and therefore she receives the token balance refunded, not the relayer. This is something relayer implementations must be aware of since there is a financial incentive for Alice to submit meta transactions that have high balances attached but will fail on Bob's shard." [5](#0-4) 

The comment in `apply_delegate_action` also acknowledges this:

> "If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction." [6](#0-5) 

### Impact Explanation

The relayer pays the deposit at transaction time. When the inner receipt fails, the deposit is refunded to Alice (the `predecessor_id` of the inner receipt), not to the relayer. Alice can deliberately craft a `DelegateAction` containing a `FunctionCall` with a large `deposit` targeting a method that will revert (e.g., a non-existent method, or a contract that panics). The relayer loses the full deposit amount to Alice. The relayer's balance is permanently reduced by the deposit; Alice's balance increases by the same amount. This is a direct fund-theft path from relayer to attacker reachable by any unprivileged NEAR account.

### Likelihood Explanation

Any user of a meta-transaction relayer service can trigger this. The attacker only needs to:
1. Craft a `DelegateAction` with a large `deposit` in a `FunctionCall` targeting a failing call.
2. Submit it to any relayer.

No special privileges, validator access, or key compromise is required. The relayer has no protocol-level protection; the only mitigation is off-chain relayer-side validation, which is not enforced by the protocol.

### Recommendation

In `apply_delegate_action`, construct the inner receipt using `ReceiptEnum::ActionV2` (which carries a `refund_to` field) and set `refund_to = Some(action_receipt.predecessor_id())` (the relayer's account ID). This ensures that if the inner receipt fails, the deposit refund is directed to the relayer who paid it, not to the DelegateAction sender.

Alternatively, the `Receipt::V0` / `ReceiptEnum::Action` path should be deprecated for delegate-spawned receipts in favor of `ActionV2` with an explicit `refund_to`.

### Proof of Concept

1. Relayer `R` has balance `B`.
2. Alice (`A`) constructs a `DelegateAction`:
   - `sender_id = A`, `receiver_id = some_contract`
   - Inner action: `FunctionCall { method_name: "nonexistent", deposit: D, gas: G }`
   - Signs with her key.
3. Alice sends the `SignedDelegateAction` to relayer `R`.
4. `R` wraps it in a transaction: `signer = R`, `receiver = A`, action = `Delegate(signed_delegate_action)`.
5. On Alice's shard, `apply_delegate_action` runs:
   - Inner receipt created: `predecessor_id = A`, `signer_id = R`.
   - Relayer's balance was debited `D` (deposit) + gas at transaction time.
6. Inner receipt arrives at `some_contract`'s shard. `nonexistent` method fails.
7. `refund_unspent_gas_and_deposits` issues:
   - Deposit refund `D` → `balance_refund_receiver()` = `predecessor_id` = **Alice**.
   - Gas refund → `signer_id` = Relayer (correct).
8. Alice receives `D` yoctoNEAR. Relayer loses `D` yoctoNEAR permanently.

By choosing `D` to be the maximum the relayer will accept, Alice can drain the relayer's balance one meta-transaction at a time.

### Citations

**File:** runtime/runtime/src/actions.rs (L455-469)
```rust
    // Generate a new receipt from DelegateAction.
    let new_receipt = Receipt::V0(ReceiptV0 {
        predecessor_id: sender_id.clone(),
        receiver_id: delegate_action.receiver_id().clone(),
        receipt_id: CryptoHash::default(),

        receipt: ReceiptEnum::Action(ActionReceipt {
            signer_id: action_receipt.signer_id().clone(),
            signer_public_key: action_receipt.signer_public_key().clone(),
            gas_price: action_receipt.gas_price(),
            output_data_receivers: vec![],
            input_data_ids: vec![],
            actions: delegate_action.get_actions(),
        }),
    });
```

**File:** runtime/runtime/src/actions.rs (L471-475)
```rust
    // Note, Relayer prepaid all fees and all things required by actions: attached deposits and attached gas.
    // If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction.
    // Gas is refunded to the signer, this is Relayer.
    // Some contracts refund the deposit. Usually they refund the deposit to the predecessor and this is sender_id/Sender from DelegateAction.
    // Therefore Relayer should verify DelegateAction before submitting it because it spends the attached deposit.
```

**File:** runtime/runtime/src/lib.rs (L1269-1273)
```rust
        if deposit_refund > Balance::ZERO {
            result.new_receipts.push(Receipt::new_balance_refund(
                receipt.balance_refund_receiver(),
                deposit_refund,
            ));
```

**File:** core/primitives/src/receipt.rs (L416-426)
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
```

**File:** core/primitives/src/receipt.rs (L428-430)
```rust
    pub fn balance_refund_receiver(&self) -> &AccountId {
        self.refund_to().as_ref().unwrap_or_else(|| self.predecessor_id())
    }
```

**File:** docs/architecture/how/meta-tx.md (L237-242)
```markdown
and agrees to pay for it, so nothing wrong so far. But what if the transaction
fails execution on Bob's shard? At this point, the predecessor is `Alice` and
therefore she receives the token balance refunded, not the relayer. This is
something relayer implementations must be aware of since there is a financial
incentive for Alice to submit meta transactions that have high balances attached
but will fail on Bob's shard.
```
