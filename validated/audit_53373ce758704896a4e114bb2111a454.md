### Title
Relayer Deposit Theft via Crafted Failing Meta-Transaction Inner Actions — (File: `runtime/runtime/src/actions.rs`)

---

### Summary

In the meta-transaction (NEP-366) execution path, the relayer pays the deposit for inner actions, but when those inner actions fail on the receiver's shard, the deposit refund is unconditionally routed to the `predecessor_id` of the inner receipt — which is the **sender (Alice)**, not the relayer who funded it. An unprivileged user can exploit this to steal an arbitrarily large deposit from any relayer willing to submit the meta-transaction.

---

### Finding Description

`apply_delegate_action` constructs the inner action receipt with `predecessor_id` set to the **sender** (Alice), not the relayer:

```rust
// runtime/runtime/src/actions.rs:456-457
let new_receipt = Receipt::V0(ReceiptV0 {
    predecessor_id: sender_id.clone(),   // ← Alice, not the relayer
    receiver_id: delegate_action.receiver_id().clone(),
    ...
});
```

The code comment immediately below acknowledges the asymmetry:

```rust
// runtime/runtime/src/actions.rs:471-475
// Note, Relayer prepaid all fees and all things required by actions:
// attached deposits and attached gas.
// If something goes wrong, deposit is refunded to the predecessor,
// this is sender_id/Sender in DelegateAction.
```

`total_deposit` in `config.rs` also carries the same acknowledgement:

```rust
// runtime/runtime/src/config.rs:563-564
// Note, here Relayer pays the deposit but if actions fail, the deposit is
// refunded to Sender of DelegateAction
```

When the inner receipt fails, `refund_unspent_gas_and_deposits` computes `deposit_refund = total_deposit` and routes it via:

```rust
// runtime/runtime/src/lib.rs:1281-1285
if deposit_refund > Balance::ZERO {
    result.new_receipts.push(Receipt::new_balance_refund(
        receipt.balance_refund_receiver(),   // resolves to predecessor_id = Alice
        deposit_refund,
    ));
}
```

`balance_refund_receiver` falls back to `predecessor_id` because the inner receipt is `ReceiptEnum::Action` (not `ActionV2`), so `refund_to` is always `None`:

```rust
// core/primitives/src/receipt.rs:416-429
pub fn refund_to(&self) -> &Option<AccountId> {
    match self.receipt() {
        ReceiptEnum::Action(_) | ... => &None,   // ← always None for delegate-spawned receipts
        ReceiptEnum::ActionV2(r) | ... => &r.refund_to,
    }
}
pub fn balance_refund_receiver(&self) -> &AccountId {
    self.refund_to().as_ref().unwrap_or_else(|| self.predecessor_id())
}
```

The relayer has no mechanism to redirect the deposit refund to itself. The `ActionReceiptV2.refund_to` field exists in the protocol but `apply_delegate_action` never uses `ActionV2`, leaving the refund path permanently locked to Alice.

---

### Impact Explanation

**Stolen funds:** The relayer loses the full deposit amount attached to the inner actions. Alice can set this to any value up to the relayer's balance. The gas cost to Alice is zero (the relayer pays gas too). The net result is an unbounded transfer of NEAR from the relayer to Alice.

**Broken invariant:** The party that pays a deposit (relayer) must receive the refund if execution fails. This invariant holds for all normal transactions but is broken for meta-transactions: the payer (relayer) and the refund recipient (Alice) are different accounts with adversarial interests.

**Scope match:** "stealing or loss of funds" — relayer loses deposit; Alice gains it. Root cause is in nearcore runtime execution code, reachable from an ordinary user-submitted transaction.

---

### Likelihood Explanation

- Any general-purpose relayer that does not exhaustively simulate inner-action execution on the current chain state before submitting is vulnerable.
- Alice controls the inner actions entirely (she signs the `DelegateAction`). She can target a contract method that is guaranteed to fail (non-existent method, contract that panics, receiver account that does not exist, etc.).
- The attack requires only that a relayer be willing to submit the meta-transaction — a condition that is the entire purpose of a relayer service.
- The attack is repeatable: each submission with a fresh nonce drains another deposit from the relayer.

---

### Recommendation

1. **Use `ActionReceiptV2` with `refund_to` set to the relayer's account** in `apply_delegate_action`. The `refund_to` field already exists in the protocol (`ReceiptEnum::ActionV2`, `core/primitives/src/receipt.rs:423-424`) and `balance_refund_receiver` already consults it. Setting `refund_to = Some(action_receipt.signer_id())` (the relayer) would route failed-deposit refunds back to the relayer.

2. **Alternatively**, document and enforce a protocol-level rule that inner actions in a `DelegateAction` may not carry a non-zero deposit unless the relayer explicitly opts in via a signed acknowledgement, preventing the attack surface entirely.

---

### Proof of Concept

**Setup:**
- Relayer account: `relayer.near` with 10,000 NEAR
- Sender (attacker): `alice.near`
- Receiver: `bob.near` (has a contract deployed)

**Steps:**

1. Alice constructs a `DelegateAction` with:
   - `sender_id = alice.near`
   - `receiver_id = bob.near`
   - `actions = [FunctionCall { method_name: "nonexistent", deposit: 5000 NEAR, gas: 10 TGas }]`
   - Valid nonce and `max_block_height`
   - Signed with Alice's key

2. Alice sends the `SignedDelegateAction` to the relayer off-chain.

3. The relayer wraps it in a transaction signed by `relayer.near` and submits it. At conversion time, `total_deposit` counts the 5000 NEAR inner deposit and debits it from `relayer.near`'s balance.

4. The outer receipt reaches `alice.near`'s shard. `apply_delegate_action` verifies the signature and spawns the inner receipt with `predecessor_id = alice.near`.

5. The inner receipt reaches `bob.near`'s shard. The function call fails (`MethodNotFound`). `refund_unspent_gas_and_deposits` computes `deposit_refund = 5000 NEAR` and calls `Receipt::new_balance_refund(receipt.balance_refund_receiver(), 5000 NEAR)` where `balance_refund_receiver()` returns `alice.near`.

6. **Result:** `relayer.near` is down 5000 NEAR (plus gas); `alice.near` is up 5000 NEAR. The attack is repeatable with fresh nonces.

**Relevant code anchors:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** runtime/runtime/src/config.rs (L562-566)
```rust
        if let Some(delegate_action) = delegate_inner_action(action) {
            // Note, here Relayer pays the deposit but if actions fail, the deposit is
            // refunded to Sender of DelegateAction
            let actions = delegate_action.get_actions();
            action_balance = total_deposit(&actions)?;
```

**File:** runtime/runtime/src/lib.rs (L1281-1285)
```rust
        if deposit_refund > Balance::ZERO {
            result.new_receipts.push(Receipt::new_balance_refund(
                receipt.balance_refund_receiver(),
                deposit_refund,
            ));
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

**File:** docs/architecture/how/meta-tx.md (L236-242)
```markdown
The relayer can see what the cost will be before submitting the meta transaction
and agrees to pay for it, so nothing wrong so far. But what if the transaction
fails execution on Bob's shard? At this point, the predecessor is `Alice` and
therefore she receives the token balance refunded, not the relayer. This is
something relayer implementations must be aware of since there is a financial
incentive for Alice to submit meta transactions that have high balances attached
but will fail on Bob's shard.
```
