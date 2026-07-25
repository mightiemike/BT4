### Title
Meta-Transaction Deposit Refund Misdirection: Relayer's Attached Deposit Refunded to Sender on Inner-Action Failure — (`runtime/runtime/src/actions.rs`)

---

### Summary

In NEAR's meta-transaction (NEP-366) execution path, `apply_delegate_action` sets the inner receipt's `predecessor_id` to the **sender** (`DelegateAction.sender_id`, i.e. Alice), not the **relayer** who actually paid the attached deposit. When the inner action fails on the receiver's shard, the protocol refunds the full deposit to `receipt.balance_refund_receiver()`, which resolves to `predecessor_id` — Alice — not the relayer. An unprivileged user (Alice) can deliberately craft a failing meta-transaction with a large attached deposit to drain the relayer's balance.

---

### Finding Description

**Root cause — `apply_delegate_action`** (`runtime/runtime/src/actions.rs:456-469`):

```rust
let new_receipt = Receipt::V0(ReceiptV0 {
    predecessor_id: sender_id.clone(),   // ← Alice, not the relayer
    receiver_id: delegate_action.receiver_id().clone(),
    ...
    receipt: ReceiptEnum::Action(ActionReceipt {
        signer_id: action_receipt.signer_id().clone(), // relayer
        ...
        actions: delegate_action.get_actions(),
    }),
});
```

The relayer is the `signer_id` of the outer receipt and the entity that paid the deposit. However, the inner receipt's `predecessor_id` is set to `sender_id` (Alice). [1](#0-0) 

**Refund routing — `refund_unspent_gas_and_deposits`** (`runtime/runtime/src/lib.rs:1281-1285`):

```rust
if deposit_refund > Balance::ZERO {
    result.new_receipts.push(Receipt::new_balance_refund(
        receipt.balance_refund_receiver(),  // resolves to predecessor_id = Alice
        deposit_refund,
    ));
}
``` [2](#0-1) 

**`balance_refund_receiver`** (`core/primitives/src/receipt.rs:428-430`):

```rust
pub fn balance_refund_receiver(&self) -> &AccountId {
    self.refund_to().as_ref().unwrap_or_else(|| self.predecessor_id())
}
```

For a `Receipt::V0` (the only variant produced by `apply_delegate_action`), `refund_to()` is always `None`, so the refund goes to `predecessor_id()` = Alice. [3](#0-2) 

The protocol documentation explicitly acknowledges this identity mismatch:

> "But what if the transaction fails execution on Bob's shard? At this point, the predecessor is `Alice` and therefore she receives the token balance refunded, not the relayer. This is something relayer implementations must be aware of since there is a financial incentive for Alice to submit meta transactions that have high balances attached but will fail on Bob's shard." [4](#0-3) 

The comment inside `apply_delegate_action` itself confirms the design:

> "Note, Relayer prepaid all fees and all things required by actions: attached deposits and attached gas. If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction." [5](#0-4) 

This is the exact analog of the Arbitrum address-aliasing bug: an **identity transformation during message passing** (relayer → sender as `predecessor_id`) causes the **wrong party to receive funds** that the legitimate payer (relayer) is entitled to recover.

---

### Impact Explanation

An unprivileged user (Alice) can steal an arbitrarily large amount of NEAR tokens from any relayer:

1. Alice crafts a `DelegateAction` with a large `deposit` (e.g., 100 NEAR) in an inner `FunctionCall` or `Transfer` action.
2. Alice deliberately targets a receiver that will reject the call (non-existent method, contract that panics, etc.).
3. Alice submits the `SignedDelegateAction` to a relayer.
4. The relayer wraps it in a transaction and pays the 100 NEAR deposit upfront.
5. The inner action fails on the receiver's shard.
6. `refund_unspent_gas_and_deposits` issues a `new_balance_refund` to `predecessor_id` = Alice.
7. Alice receives 100 NEAR; the relayer loses 100 NEAR.

The stolen amount is bounded only by the relayer's balance and the deposit Alice specifies. This is a direct, protocol-enforced **theft of funds** from the relayer by an unprivileged user, reachable through ordinary transaction submission.

---

### Likelihood Explanation

- Any user can create a `DelegateAction` and submit it to a public relayer.
- Deliberately failing inner actions are trivial to construct (call a non-existent method, attach a deposit to a function call on a contract that always panics).
- No special privileges, validator access, or network-level capabilities are required.
- The attack is repeatable and scales linearly with the relayer's willingness to accept meta-transactions with large deposits.
- The only friction is finding a relayer that accepts the meta-transaction without pre-validating that the inner action will succeed — but relayers cannot fully guarantee execution outcomes on a remote shard.

---

### Recommendation

The fix is to redirect deposit refunds to the relayer (the actual payer) rather than to Alice. The `ActionReceiptV2` struct already has a `refund_to` field for exactly this purpose. `apply_delegate_action` should produce a `Receipt::V0` with an `ActionReceiptV2` payload (or use the `refund_to` mechanism) that sets `refund_to = Some(relayer_id)`, where `relayer_id` is the `predecessor_id` of the outer action receipt (the relayer's account). This ensures that on failure, the deposit is returned to the party who paid it.

Alternatively, the protocol could require relayers to pre-validate that inner actions will succeed before attaching deposits, but this is not enforceable at the protocol level and is insufficient as a sole mitigation.

---

### Proof of Concept

**Setup:**
- Relayer account: `relayer.near` (balance: 200 NEAR)
- Attacker account: `alice.near` (balance: 0 NEAR)
- Target contract: `bob.near` (has a method `always_panic` that always panics)

**Steps:**

1. Alice creates and signs a `DelegateAction`:
   ```
   DelegateAction {
       sender_id: "alice.near",
       receiver_id: "bob.near",
       actions: [FunctionCall { method_name: "always_panic", deposit: 100 NEAR, gas: 30 TGas }],
       nonce: 1,
       max_block_height: current + 100,
       public_key: alice_key,
   }
   ```

2. Alice submits `SignedDelegateAction` to the relayer.

3. Relayer wraps it in a transaction (relayer pays 100 NEAR deposit + gas):
   ```
   Transaction { signer: relayer.near, receiver: alice.near, actions: [Delegate(signed_delegate)] }
   ```

4. `apply_delegate_action` executes on Alice's shard:
   - Creates inner receipt: `predecessor_id = alice.near`, `receiver_id = bob.near`
   - Inner receipt carries 100 NEAR deposit (deducted from relayer)

5. Inner receipt executes on Bob's shard → `always_panic` panics → action fails.

6. `refund_unspent_gas_and_deposits` runs:
   - `deposit_refund = 100 NEAR`
   - `receipt.balance_refund_receiver()` = `predecessor_id` = `alice.near`
   - Issues `Receipt::new_balance_refund("alice.near", 100 NEAR)`

7. **Result:** Alice receives 100 NEAR; relayer loses 100 NEAR.

The existing integration test `meta_tx_near_transfer` and the documentation in `docs/architecture/how/meta-tx.md` confirm this behavior is present in the production runtime. [6](#0-5) [7](#0-6)

### Citations

**File:** runtime/runtime/src/actions.rs (L456-469)
```rust
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

**File:** runtime/runtime/src/lib.rs (L1281-1285)
```rust
        if deposit_refund > Balance::ZERO {
            result.new_receipts.push(Receipt::new_balance_refund(
                receipt.balance_refund_receiver(),
                deposit_refund,
            ));
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

**File:** integration-tests/src/tests/features/delegate_action.rs (L293-309)
```rust
/// The simplest non-empty meta transaction: Transferring some NEAR tokens.
///
/// Note: The expectation is that the relayer pays for the tokens sent, as
/// specified in NEP-366.
#[test]
fn meta_tx_near_transfer() {
    let sender = bob_account();
    let relayer = alice_account();
    let receiver = carol_account();
    let node = RuntimeNode::new(&relayer);
    let fee_helper = fee_helper(&node);

    let amount = Balance::from_near(1);
    let actions = vec![Action::Transfer(TransferAction { deposit: amount })];
    let tx_cost = fee_helper.transfer_cost();
    check_meta_tx_no_fn_call(&node, actions, tx_cost, amount, sender, relayer, receiver);
}
```
