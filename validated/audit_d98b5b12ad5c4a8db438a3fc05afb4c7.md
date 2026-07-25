### Title
Deposit Refund Misdirected to Sender Instead of Relayer in Meta-Transaction Delegate Actions — (File: runtime/runtime/src/actions.rs)

### Summary

In NEAR's meta-transaction (delegate action) implementation, when an inner receipt fails on the receiver's shard, the attached deposit is refunded to the sender (`Alice`) rather than the relayer who actually paid it. `apply_delegate_action` sets `predecessor_id = sender_id` in the spawned receipt, and deposit refunds always go to `predecessor_id`. An unprivileged user can exploit this to drain deposits from relayers by crafting meta-transactions with large deposits that are designed to fail.

### Finding Description

When `apply_delegate_action` processes a `DelegateAction`, it creates a new receipt:

```rust
let new_receipt = Receipt::V0(ReceiptV0 {
    predecessor_id: sender_id.clone(),          // Alice — the delegate signer
    receiver_id: delegate_action.receiver_id().clone(),
    receipt_id: CryptoHash::default(),
    receipt: ReceiptEnum::Action(ActionReceipt {
        signer_id: action_receipt.signer_id().clone(), // Relayer — the tx signer
        signer_public_key: action_receipt.signer_public_key().clone(),
        ...
    }),
});
``` [1](#0-0) 

The deposit for any `Transfer` or `FunctionCall` action inside the `DelegateAction` is charged to the relayer when the outer transaction is converted to a receipt. However, when the inner receipt fails on the receiver's shard, `refund_unspent_gas_and_deposits` creates a deposit refund receipt targeting `receipt.balance_refund_receiver()`, which resolves to `receipt.predecessor_id()` — Alice — not the relayer who paid. [2](#0-1) 

The `balance_refund_receiver` helper confirms this: it returns `refund_to` if set (only available in `ActionReceiptV2`), otherwise falls back to `predecessor_id`. [3](#0-2) 

This is the NEAR analog of the external bug: the wrong account identity is used for a critical financial routing decision. In the external report, `tx.origin` (transaction originator) was used where `msg.sender` (actual caller) should be. Here, `predecessor_id` (Alice, the "msg.sender" of the inner receipt) is used for deposit refund routing where `signer_id` (the relayer, the actual payer — the "tx.origin" equivalent) should be used.

The NEAR documentation explicitly acknowledges this asymmetry: [4](#0-3) 

The comment in `apply_delegate_action` also acknowledges it: [5](#0-4) 

Despite being documented, there are **no on-chain guards** that prevent Alice from exploiting this. The protocol unconditionally routes the deposit refund to `predecessor_id` with no mechanism for the relayer to reclaim it.

### Impact Explanation

The relayer pays the deposit (charged at transaction-to-receipt conversion time) but Alice receives the full deposit refund when the inner receipt fails. Alice can deliberately craft a `DelegateAction` containing a `Transfer` or `FunctionCall` with a large deposit to a receiver that will reject it (non-existent account, contract that panics, etc.). The relayer loses the entire deposit amount. This is a direct, on-chain loss of funds for the relayer, reachable through ordinary user actions with no privileged access required.

### Likelihood Explanation

Any user interacting with a relayer can trigger this. The relayer cannot always predict execution failure: contract state can change between submission and execution (race condition), or Alice can deliberately target a receiver she knows will fail. The attack requires only that Alice submit a `DelegateAction` with an attached deposit to a failing receiver — a standard, unprivileged operation. The financial incentive scales linearly with the deposit size.

### Recommendation

When `apply_delegate_action` creates the inner receipt, it should use `ActionReceiptV2` (which supports `refund_to`) and set `refund_to = Some(relayer_id)` so that deposit refunds are directed to the relayer who paid, not to Alice. The relayer's account ID is available as `action_receipt.predecessor_id()` (the predecessor of the receipt being processed on Alice's shard).

Alternatively, a protocol-level `refund_to` field could be added to `DelegateAction` itself, allowing the relayer to specify the deposit refund recipient at submission time.

### Proof of Concept

1. Alice creates a `DelegateAction`: `sender=Alice, receiver=NonExistentBob, actions=[Transfer{deposit=100 NEAR}]`, signs it with her key.
2. Alice sends the signed `DelegateAction` to a relayer.
3. The relayer wraps it in a transaction (`signer=Relayer, receiver=Alice`), paying 100 NEAR deposit from its own account.
4. On Alice's shard, `apply_delegate_action` spawns a new receipt: `predecessor=Alice, receiver=NonExistentBob, signer=Relayer, actions=[Transfer{deposit=100 NEAR}]`.
5. On Bob's shard, the `Transfer` fails (account does not exist).
6. `refund_unspent_gas_and_deposits` calls `Receipt::new_balance_refund(receipt.balance_refund_receiver(), 100 NEAR)` → `receipt.predecessor_id()` = Alice.
7. Alice receives 100 NEAR. The relayer's 100 NEAR is permanently lost. [6](#0-5)

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
