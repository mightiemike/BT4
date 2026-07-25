### Title
Deposit Refund Misdirection in Meta Transactions Allows DelegateAction Sender to Drain Relayer's Attached Deposit — (File: `runtime/runtime/src/actions.rs`)

---

### Summary

In `apply_delegate_action`, the inner receipt spawned from a `DelegateAction` is created with `predecessor_id = sender_id` (Alice). When that inner receipt fails on the receiver's shard, the runtime's deposit-refund path sends the full attached deposit back to Alice — not to the relayer who actually paid for it. An unprivileged user (Alice) can deliberately craft a `DelegateAction` with a large deposit attached to a function call that will fail, causing the relayer to permanently lose the deposit to Alice.

---

### Finding Description

In `apply_delegate_action` (`runtime/runtime/src/actions.rs:456–469`), the inner receipt is constructed as:

```rust
let new_receipt = Receipt::V0(ReceiptV0 {
    predecessor_id: sender_id.clone(),          // Alice
    receiver_id: delegate_action.receiver_id(), // Bob
    receipt: ReceiptEnum::Action(ActionReceipt {
        signer_id: action_receipt.signer_id(),  // Relayer
        ...
        actions: delegate_action.get_actions(), // may include FunctionCall(deposit=X)
    }),
});
``` [1](#0-0) 

The relayer's balance is debited for the full deposit `X` at transaction-submission time (via `verify_and_charge_transaction`). When the inner receipt fails on Bob's shard, `refund_unspent_gas_and_deposits` generates the deposit refund receipt:

```rust
if deposit_refund > Balance::ZERO {
    result.new_receipts.push(Receipt::new_balance_refund(
        receipt.balance_refund_receiver(), // = receipt.predecessor_id() = Alice
        deposit_refund,
    ));
}
``` [2](#0-1) 

`balance_refund_receiver()` resolves to `predecessor_id()` unless an explicit `refund_to` override is set:

```rust
pub fn balance_refund_receiver(&self) -> &AccountId {
    self.refund_to().as_ref().unwrap_or_else(|| self.predecessor_id())
}
``` [3](#0-2) 

Because `apply_delegate_action` uses the legacy `ReceiptEnum::Action` (V0) variant — which has no `refund_to` field — the refund always resolves to `predecessor_id = Alice`. The gas refund correctly goes to the relayer (`signer_id`), but the deposit refund does not.

The code comment at the creation site acknowledges the asymmetry but treats it as the relayer's responsibility to avoid:

```
// Note, Relayer prepaid all fees and all things required by actions: attached deposits and attached gas.
// If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction.
// Gas is refunded to the signer, this is Relayer.
// Therefore Relayer should verify DelegateAction before submitting it because it spends the attached deposit.
``` [4](#0-3) 

The architecture documentation explicitly names the resulting attack surface:

> "there is a financial incentive for Alice to submit meta transactions that have high balances attached but will fail on Bob's shard." [5](#0-4) 

---

### Impact Explanation

**Broken invariant:** The party who pays an attached deposit must receive the refund when the action fails. In a normal transaction `signer == predecessor`, so this holds. In a meta transaction the relayer is the signer (and payer) but Alice is the predecessor of the inner receipt, breaking the invariant.

**Exact corrupted value:** For every failing meta transaction carrying a deposit of `D` yoctoNEAR, the relayer's balance is permanently reduced by `D` and Alice's balance is permanently increased by `D`. There is no cap on `D`; Alice can attach up to the maximum allowed deposit per function call.

**Scope match:** Stealing or loss of funds — the relayer (an ordinary NEAR account, not a privileged role) loses real NEAR tokens to Alice.

---

### Likelihood Explanation

Alice needs only to:
1. Craft a `DelegateAction` with a large `deposit` in a `FunctionCall` targeting a method that will fail (non-existent method, panic, or any revert condition Alice controls).
2. Hand the signed `DelegateAction` to any relayer.

No special privilege, no validator access, no network-level capability is required. The attack is deterministic and repeatable. The only constraint is finding a relayer willing to submit the transaction, which is the normal operating model for meta transactions.

---

### Recommendation

Replace the `ReceiptEnum::Action` (V0) inner receipt in `apply_delegate_action` with `ReceiptEnum::ActionV2` (`ActionReceiptV2`), which carries the `refund_to: Option<AccountId>` field, and set `refund_to = Some(action_receipt.signer_id().clone())` (the relayer). This routes deposit refunds to the relayer while leaving gas refunds on the existing `signer_id` path, restoring the invariant that the payer receives the refund. [6](#0-5) 

Alternatively, document a protocol-level guarantee that relayers must never attach deposits to inner actions unless they accept the risk of losing them to the sender — but this is a weaker mitigation that does not fix the broken invariant.

---

### Proof of Concept

**Actors:**
- `relayer.near` — submits the meta transaction, holds 1 000 NEAR
- `alice.near` — crafts the `DelegateAction`, holds 0 NEAR
- `bob.near` — target contract with no method named `"steal"`

**Steps:**

1. Alice constructs and signs:
   ```
   DelegateAction {
       sender_id:       "alice.near",
       receiver_id:     "bob.near",
       actions:         [FunctionCall { method: "steal", deposit: 999 NEAR, gas: 300 TGas }],
       nonce:           <valid>,
       max_block_height: <future>,
       public_key:      <alice's key>,
   }
   ```

2. Alice sends the `SignedDelegateAction` to the relayer off-chain.

3. The relayer wraps it in a transaction (`signer = relayer.near`, `receiver = alice.near`) and submits it. `verify_and_charge_transaction` debits 999 NEAR + gas fees from `relayer.near`.

4. On Alice's shard, `apply_delegate_action` creates the inner receipt:
   - `predecessor_id = alice.near`
   - `signer_id = relayer.near`
   - `actions = [FunctionCall(deposit=999 NEAR)]`

5. On Bob's shard, the receipt fails (`MethodNotFound`). `refund_unspent_gas_and_deposits` emits:
   - Deposit refund → `alice.near` (predecessor) — **999 NEAR**
   - Gas refund → `relayer.near` (signer) — small amount

6. **Result:** `relayer.near` net loss ≈ 999 NEAR; `alice.near` net gain ≈ 999 NEAR.

The attack is atomic from Alice's perspective: she signs once, hands the action to the relayer, and the protocol guarantees the refund path regardless of what the relayer does next.

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

**File:** core/primitives/src/receipt.rs (L622-641)
```rust
pub struct ActionReceiptV2 {
    /// A signer of the original transaction
    pub signer_id: AccountId,
    /// The receiver of any balance refunds form this receipt if it is different from receiver_id.
    pub refund_to: Option<AccountId>,
    /// An access key which was used to sign the original transaction
    pub signer_public_key: PublicKey,
    /// A gas_price which has been used to buy gas in the original transaction
    pub gas_price: Balance,
    /// If present, where to route the output data
    pub output_data_receivers: Vec<DataReceiver>,
    /// A list of the input data dependencies for this Receipt to process.
    /// If all `input_data_ids` for this receipt are delivered to the account
    /// that means we have all the `ReceivedData` input which will be than converted to a
    /// `PromiseResult::Successful(value)` or `PromiseResult::Failed`
    /// depending on `ReceivedData` is `Some(_)` or `None`
    pub input_data_ids: Vec<CryptoHash>,
    /// A list of actions to process when all input_data_ids are filled
    pub actions: Vec<Action>,
}
```

**File:** docs/architecture/how/meta-tx.md (L238-242)
```markdown
fails execution on Bob's shard? At this point, the predecessor is `Alice` and
therefore she receives the token balance refunded, not the relayer. This is
something relayer implementations must be aware of since there is a financial
incentive for Alice to submit meta transactions that have high balances attached
but will fail on Bob's shard.
```
