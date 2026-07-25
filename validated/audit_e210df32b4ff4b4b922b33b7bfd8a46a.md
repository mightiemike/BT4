### Title
Deposit Refund Sent to DelegateAction Sender (Alice) Instead of Relayer Who Paid — (`runtime/runtime/src/actions.rs`)

### Summary

In meta-transactions (NEP-366 `DelegateAction`), the relayer pays the deposit for inner actions at transaction-processing time. When those inner actions fail on the receiver's shard, the deposit refund is sent to the `DelegateAction` sender (Alice) — the `predecessor_id` of the spawned inner receipt — rather than to the relayer who actually funded the deposit. An unprivileged user can exploit this to drain the relayer's balance by crafting a meta-transaction with a large deposit that is guaranteed to fail.

### Finding Description

**How the deposit is charged.** When the relayer submits a meta-transaction, `verify_and_charge_transaction` deducts `total_deposit(actions)` from the relayer's account. `total_deposit` recurses into the `DelegateAction`'s inner actions and sums their deposits: [1](#0-0) 

So the relayer's balance is reduced by the full inner deposit at submission time.

**How the inner receipt is constructed.** In `apply_delegate_action`, the spawned receipt is built with `predecessor_id = sender_id` (Alice) and `signer_id = action_receipt.signer_id()` (the relayer): [2](#0-1) 

**How the deposit refund is routed.** When the inner receipt fails on Bob's shard, `refund_unspent_gas_and_deposits` computes `deposit_refund = total_deposit` and pushes a balance-refund receipt to `receipt.balance_refund_receiver()`: [3](#0-2) 

`balance_refund_receiver()` returns `refund_to` if set, otherwise `predecessor_id`: [4](#0-3) 

Because the inner receipt was created with `predecessor_id = Alice` and `refund_to = None`, the deposit refund goes to **Alice**, not the relayer. The gas refund, by contrast, correctly goes to `action_receipt.signer_id()` (the relayer): [5](#0-4) 

**The invariant broken.** The entity whose balance was debited for the deposit (the relayer) is not the entity that receives the deposit refund on failure (Alice). This is the direct nearcore analog of the external bug: the refund goes to the wrong party.

The nearcore documentation explicitly acknowledges this asymmetry and the resulting financial incentive: [6](#0-5) 

The code has no guard or correction for this: the inner receipt is always created as `ReceiptEnum::Action` (not `ActionV2`), so the `refund_to` field is structurally unavailable on that receipt type: [7](#0-6) 

### Impact Explanation

The relayer loses the full deposit attached to any inner action that fails on the receiver's shard. Alice receives those tokens. Because the relayer cannot verify at submission time that Alice's inner function call will succeed (the call executes on a remote shard), Alice can reliably extract arbitrary amounts from any relayer willing to relay her meta-transactions. The loss is proportional to the deposit Alice attaches to the inner action.

### Likelihood Explanation

Any user who can submit a meta-transaction to a relayer can trigger this. The attacker (Alice) needs only to:
1. Attach a large deposit to an inner `FunctionCall` action.
2. Ensure the call will fail on the receiver's shard (e.g., call a non-existent method, or a method that panics after receiving the deposit).
3. Convince or pay a relayer to submit the meta-transaction.

No privileged access, validator control, or key compromise is required. The attack is repeatable.

### Recommendation

Two complementary fixes:

1. **Use `ActionReceiptV2` with `refund_to` set to the relayer.** In `apply_delegate_action`, construct the inner receipt as `ReceiptEnum::ActionV2(ActionReceiptV2 { refund_to: Some(relayer_id), … })`. `balance_refund_receiver()` already checks `refund_to` first, so this would redirect the deposit refund to the relayer without changing the `predecessor_id` semantics that contracts observe.

2. **Alternatively, document and enforce a relayer-side check.** Relayers should simulate the inner call before submission and refuse to relay meta-transactions whose inner actions are likely to fail with a non-zero deposit. This is a mitigation, not a fix.

### Proof of Concept

```
Actors:
  relayer  — submits the meta-transaction, pays all costs
  alice    — crafts the DelegateAction
  bob      — receiver contract (no method "drain" exists)

Step 1: Alice creates and signs a DelegateAction:
  DelegateAction {
    sender_id:   "alice",
    receiver_id: "bob",
    actions: [FunctionCall { method: "drain", deposit: 100 NEAR, gas: 100 TGas }],
    …
  }

Step 2: Alice sends the signed DelegateAction to the relayer.

Step 3: Relayer wraps it in a transaction:
  Transaction {
    signer_id:   "relayer",
    receiver_id: "alice",
    actions: [Delegate(signed_delegate_action)],
  }
  → relayer's balance is debited 100 NEAR (deposit) + gas costs.

Step 4: On alice's shard, apply_delegate_action spawns:
  Receipt {
    predecessor_id: "alice",   ← deposit refund will go here on failure
    receiver_id:    "bob",
    signer_id:      "relayer", ← gas refund goes here
    actions: [FunctionCall { method: "drain", deposit: 100 NEAR }],
  }

Step 5: On bob's shard, "drain" does not exist → receipt fails.
  refund_unspent_gas_and_deposits:
    deposit_refund = 100 NEAR → Receipt::new_balance_refund("alice", 100 NEAR)
    gas_balance_refund        → Receipt::new_gas_refund("relayer", …)

Result:
  alice   gains 100 NEAR (deposit refund)
  relayer loses 100 NEAR (deposit) + gas costs, recovers only unused gas
```

The root cause is in `apply_delegate_action` at `runtime/runtime/src/actions.rs` lines 456–469, where the inner receipt is constructed with `predecessor_id = sender_id` (Alice) and no `refund_to` override, causing `balance_refund_receiver()` to return Alice instead of the relayer.

### Citations

**File:** runtime/runtime/src/config.rs (L557-574)
```rust
/// Get the total sum of deposits for given actions.
pub fn total_deposit(actions: &[Action]) -> Result<Balance, IntegerOverflowError> {
    let mut total_balance = Balance::ZERO;
    for action in actions {
        let action_balance;
        if let Some(delegate_action) = delegate_inner_action(action) {
            // Note, here Relayer pays the deposit but if actions fail, the deposit is
            // refunded to Sender of DelegateAction
            let actions = delegate_action.get_actions();
            action_balance = total_deposit(&actions)?;
        } else {
            action_balance = action.get_deposit_balance();
        }

        total_balance = safe_add_balance(total_balance, action_balance)?;
    }
    Ok(total_balance)
}
```

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

**File:** runtime/runtime/src/lib.rs (L1281-1286)
```rust
        if deposit_refund > Balance::ZERO {
            result.new_receipts.push(Receipt::new_balance_refund(
                receipt.balance_refund_receiver(),
                deposit_refund,
            ));
        }
```

**File:** runtime/runtime/src/lib.rs (L1287-1295)
```rust
        if gas_balance_refund > Balance::ZERO {
            // Gas refunds refund the allowance of the access key, so if the key exists on the
            // account it will increase the allowance by the refund amount.
            result.new_receipts.push(Receipt::new_gas_refund(
                &action_receipt.signer_id(),
                gas_balance_refund,
                action_receipt.signer_public_key().clone(),
            ));
        }
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

**File:** docs/architecture/how/meta-tx.md (L225-242)
```markdown
## Balance refunds in meta transactions

Unlike gas refunds, the protocol sends balance refunds to the predecessor
(a.k.a. sender) of the receipt. This makes sense, as we deposit the attached
balance to the receiver, who has to explicitly reattach a new balance to new
receipts they might spawn.

In the world of meta transactions, this assumption is also challenged. If an
inner action requires an attached balance (for example a transfer action) then
this balance is taken from the relayer.

The relayer can see what the cost will be before submitting the meta transaction
and agrees to pay for it, so nothing wrong so far. But what if the transaction
fails execution on Bob's shard? At this point, the predecessor is `Alice` and
therefore she receives the token balance refunded, not the relayer. This is
something relayer implementations must be aware of since there is a financial
incentive for Alice to submit meta transactions that have high balances attached
but will fail on Bob's shard.
```
