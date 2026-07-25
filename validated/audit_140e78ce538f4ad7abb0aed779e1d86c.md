### Title
Meta-Transaction Deposit Refund Misdirected to Sender Instead of Relayer on Inner Receipt Failure — (`File: runtime/runtime/src/actions.rs`)

### Summary

When a `DelegateAction` (NEP-366 meta-transaction) inner receipt fails execution, the attached deposit is refunded to the `predecessor_id` of the inner receipt — which is the **sender** (`Alice`) — not the **relayer** who actually paid the deposit. An unprivileged user can craft a `DelegateAction` with a large attached deposit designed to fail on the receiver's shard, causing the relayer to lose the full deposit while the sender recovers it. This is a direct, protocol-level loss of funds with no existing guard.

---

### Finding Description

In `apply_delegate_action`, the inner receipt spawned from a `DelegateAction` is constructed with `predecessor_id: sender_id.clone()` (Alice): [1](#0-0) 

The comment at line 471–475 explicitly acknowledges the asymmetry:

> "If something goes wrong, deposit is refunded to the predecessor, this is `sender_id`/Sender in DelegateAction. Gas is refunded to the signer, this is Relayer." [2](#0-1) 

When the inner receipt fails, `refund_unspent_gas_and_deposits` issues a balance-refund receipt via `Receipt::new_balance_refund(receipt.balance_refund_receiver(), deposit_refund)`: [3](#0-2) 

`balance_refund_receiver()` returns the `predecessor_id` of the receipt — which is Alice, not the relayer. The relayer's balance was debited for the deposit at transaction conversion time (in `verify_and_charge_transaction`), but the refund flows to Alice.

The documentation in `docs/architecture/how/meta-tx.md` confirms this is a known protocol property with no protocol-level fix: [4](#0-3) 

---

### Impact Explanation

The relayer pays the full attached deposit upfront. If the inner receipt fails (e.g., calling a non-existent method, or a contract that panics), the deposit is refunded to Alice — not the relayer. The relayer suffers a direct, unrecoverable loss equal to the deposit amount. There is no protocol-level cap on the deposit size; it is bounded only by the relayer's balance. This constitutes **stealing or loss of funds** from an unprivileged attacker's perspective.

---

### Likelihood Explanation

High. Any user who can sign a `DelegateAction` can exploit this:
1. No special privilege is required — any NEAR account can create and sign a `DelegateAction`.
2. The attacker only needs to find a relayer service willing to submit the transaction (many public relayers exist).
3. The failure condition is trivially engineered: target a receiver with no deployed contract, or call a non-existent method.
4. The attack is repeatable with different nonces.

---

### Proof of Concept

1. Alice creates a `DelegateAction`:
   - `sender_id = alice.near`
   - `receiver_id = bob.near` (no contract deployed)
   - Inner action: `FunctionCall { method_name: "nonexistent", deposit: 10 NEAR, gas: 30 TGas }`
2. Alice signs the `DelegateAction` and submits it to a relayer.
3. The relayer wraps it in a transaction (signer = relayer), paying 10 NEAR deposit + gas upfront.
4. On Alice's shard, `apply_delegate_action` spawns the inner receipt with `predecessor_id = alice.near`.
5. On Bob's shard, the function call fails (`MethodNotFound`).
6. `refund_unspent_gas_and_deposits` issues `Receipt::new_balance_refund(alice.near, 10 NEAR)`.
7. Alice receives 10 NEAR. The relayer loses 10 NEAR.

The exact corrupted value is `deposit_refund` sent to `alice.near` instead of `relayer.near`: [3](#0-2) [5](#0-4) 

---

### Recommendation

The deposit refund for a failed inner receipt spawned by `apply_delegate_action` should be directed to the **signer** of the outer receipt (the relayer, `action_receipt.signer_id()`), not the `predecessor_id` (Alice). Concretely, the inner receipt should carry a custom `balance_refund_receiver` pointing to the relayer, or `apply_delegate_action` should emit the deposit-refund receipt directly to `action_receipt.signer_id()` on failure, bypassing the standard `predecessor_id`-based refund path.

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
