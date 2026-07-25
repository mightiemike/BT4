### Title
Deposit Refund in `apply_delegate_action` Is Sent to the User (Alice), Not the Relayer Who Paid — (`runtime/runtime/src/actions.rs`)

### Summary

In nearcore's meta-transaction (NEP-366) execution path, `apply_delegate_action` creates the inner action receipt with `predecessor_id` set to the **user's account** (`sender_id` / Alice), not the relayer who actually funded the attached deposit. When the inner receipt fails on the receiver's shard, the deposit refund is sent to `predecessor_id` — Alice — not to the relayer who paid it. An unprivileged user can deliberately craft a `DelegateAction` with a large attached deposit that is designed to fail, causing the relayer to lose the entire deposit while the user receives it as a refund.

### Finding Description

In `apply_delegate_action` (`runtime/runtime/src/actions.rs`), the inner receipt spawned from a `DelegateAction` is constructed as:

```rust
let new_receipt = Receipt::V0(ReceiptV0 {
    predecessor_id: sender_id.clone(),          // Alice, NOT the relayer
    receiver_id: delegate_action.receiver_id().clone(),
    ...
    receipt: ReceiptEnum::Action(ActionReceipt {
        signer_id: action_receipt.signer_id().clone(), // relayer
        ...
    }),
});
``` [1](#0-0) 

When this inner receipt fails, `refund_unspent_gas_and_deposits` in `runtime/runtime/src/lib.rs` calls:

```rust
result.new_receipts.push(Receipt::new_balance_refund(
    receipt.balance_refund_receiver(),
    deposit_refund,
));
``` [2](#0-1) 

`balance_refund_receiver()` resolves to `predecessor_id()` for `Receipt::V0` with `ReceiptEnum::Action` (since `refund_to` is only available on `ActionV2`):

```rust
pub fn balance_refund_receiver(&self) -> &AccountId {
    self.refund_to().as_ref().unwrap_or_else(|| self.predecessor_id())
}
``` [3](#0-2) 

Because `predecessor_id` of the inner receipt is `sender_id` (Alice), the deposit refund goes to Alice, not to the relayer who deducted the deposit from their own balance.

The nearcore documentation explicitly acknowledges this behavior:

> "But what if the transaction fails execution on Bob's shard? At this point, the predecessor is `Alice` and therefore she receives the token balance refunded, not the relayer. This is something relayer implementations must be aware of since there is a financial incentive for Alice to submit meta transactions that have high balances attached but will fail on Bob's shard." [4](#0-3) 

The code comment in `apply_delegate_action` also confirms the relayer pays but Alice receives:

> "Note, Relayer prepaid all fees and all things required by actions: attached deposits and attached gas. If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction." [5](#0-4) 

### Impact Explanation

The relayer pays the deposit attached to inner actions (e.g., a `FunctionCall` with `deposit: 10 NEAR`). If the inner receipt fails on the receiver's shard, the full deposit is refunded to Alice (the `predecessor_id` of the inner receipt), not to the relayer. The relayer suffers a direct, permanent loss of the deposited NEAR tokens equal to the deposit amount. This is a **stealing of funds** from the relayer by an unprivileged user.

### Likelihood Explanation

Any user who can reach a relayer service can exploit this. The attack requires:
1. Alice crafts a `DelegateAction` with a large `deposit` attached to a `FunctionCall` targeting a non-existent method or a contract that will revert.
2. Alice submits it to a relayer.
3. The relayer submits the meta-transaction, paying the deposit.
4. The inner receipt fails on the receiver's shard.
5. Alice receives the full deposit as a balance refund.

No privileged access, validator control, or key compromise is required. The attack is repeatable and scales with the deposit amount the relayer is willing to forward.

### Recommendation

In `apply_delegate_action`, set the `refund_to` field of the inner receipt to the relayer's account (`action_receipt.signer_id()`), so that deposit refunds on failure are returned to the party who paid. This requires using `ReceiptEnum::ActionV2` (which carries the `refund_to` field) for the inner receipt generated from a `DelegateAction`, rather than `ReceiptEnum::Action` (V0). Alternatively, enforce at the protocol level that the relayer's address is used as the deposit refund target for all receipts spawned from `DelegateAction`.

### Proof of Concept

1. Alice creates a `DelegateAction` with `sender_id = alice`, `receiver_id = bob`, inner action = `FunctionCall { method_name: "nonexistent", deposit: 100 NEAR, gas: 100 TGas }`.
2. Alice signs it and sends it off-chain to a relayer.
3. Relayer wraps it in a transaction: `signer = relayer`, `receiver = alice`, action = `Delegate(signed_delegate_action)`.
4. On Alice's shard, `apply_delegate_action` executes: inner receipt is created with `predecessor_id = alice`, `signer_id = relayer`. The relayer's balance is debited 100 NEAR for the deposit.
5. Inner receipt is routed to Bob's shard. `nonexistent` method fails.
6. `refund_unspent_gas_and_deposits` fires: `deposit_refund = 100 NEAR`, `balance_refund_receiver() = predecessor_id() = alice`.
7. Alice receives 100 NEAR. Relayer loses 100 NEAR.

The `DelegateAction` struct and its execution path confirm this flow: [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** runtime/runtime/src/lib.rs (L1269-1283)
```rust
        if deposit_refund > Balance::ZERO {
            result.new_receipts.push(Receipt::new_balance_refund(
                receipt.balance_refund_receiver(),
                deposit_refund,
            ));
        }
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

**File:** core/primitives/src/action/delegate.rs (L46-64)
```rust
pub struct DelegateAction {
    /// Signer of the delegated actions
    pub sender_id: AccountId,
    /// Receiver of the delegated actions.
    pub receiver_id: AccountId,
    /// List of actions to be executed.
    ///
    /// With the meta transactions MVP defined in NEP-366, nested
    /// DelegateActions are not allowed. A separate type is used to enforce it.
    pub actions: Vec<NonDelegateAction>,
    /// Nonce to ensure that the same delegate action is not sent twice by a
    /// relayer and should match for given account's `public_key`.
    /// After this action is processed it will increment.
    pub nonce: Nonce,
    /// The maximal height of the block in the blockchain below which the given DelegateAction is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
    pub public_key: PublicKey,
}
```
