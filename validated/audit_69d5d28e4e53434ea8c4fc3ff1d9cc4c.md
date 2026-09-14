### Title
Meta-transaction deposit refunds are misdirected to the DelegateAction sender instead of the relayer who paid for them - (File: `runtime/runtime/src/actions.rs`)

### Summary
`apply_delegate_action` builds the inner-action receipt as a plain `Receipt::V0`/`ActionReceipt` (not the newer `ActionReceiptV2`, which carries a `refund_to` field). Because of this, if the inner actions carried an attached deposit and fail on the receiver's shard, the deposit refund goes to `predecessor_id` (the DelegateAction's `sender_id`, i.e. the party on whose behalf the relayer acted), even though the relayer actually paid for that deposit out of its own balance. This exactly mirrors the reported Axelar bug class: the entity that pays (relayer / `msg.sender`-equivalent) is not the entity that gets refunded (sender/token-owner-equivalent), because the function that constructs the downstream request has no "refund to" parameter distinct from the nominal sender.

### Finding Description
In NEAR's meta-transaction flow (NEP-366), Alice signs a `DelegateAction` and a relayer wraps it in a transaction that it signs and pays for. On the relayer's shard, `apply_delegate_action` (`runtime/runtime/src/actions.rs:453-535`) constructs a brand new receipt to send the inner actions to the destination: [1](#0-0) 

The comment directly above the receipt construction documents the issue: [2](#0-1) 

The new receipt is a `Receipt::V0(ReceiptV0 { predecessor_id: sender_id.clone(), ... })`, so its `predecessor_id` is Alice's account (`sender_id`), not the relayer. When this receipt reaches the receiver and any of its inner actions (e.g. a `Transfer` or a function call with `deposit`) fails, `refund_unspent_gas_and_deposits` (`runtime/runtime/src/lib.rs:1284-1419`) issues a deposit-refund receipt to `receipt.balance_refund_receiver()`: [3](#0-2) 

`balance_refund_receiver()` returns `refund_to` if set, otherwise falls back to `predecessor_id()`. Because the delegate-action receipt is built as a plain V0 `ActionReceipt`/`ReceiptEnum::Action` (not `ReceiptEnum::ActionV2`/`ActionReceiptV2`, which is the only variant that carries `refund_to`), `refund_to()` is `None` for it (`core/primitives/src/receipt.rs:416-426`), and the fallback returns `predecessor_id()` — i.e. Alice, the DelegateAction sender — even though the relayer funded the deposit.

Gas refunds are handled correctly (they go to `signer_id`, which is preserved as the relayer's signer identity through the delegate receipt), but deposit refunds are not, as the code comment itself acknowledges: "Gas is refunded to the signer, this is Relayer... deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction."

The protocol already has the mechanism to fix this class of bug — the `refund_to`/`refund_to_account_id` host function and `ActionReceiptV2.refund_to` field, added for the general promise API (`core/primitives/src/receipt.rs:416-430`, `runtime/near-vm-runner/src/logic/context.rs`, tested in `runtime/runtime/tests/test_async_calls.rs:1204-1296`, `test_refund_to`). However, `apply_delegate_action` never sets `refund_to` when constructing the inner-action receipt, so the meta-transaction path does not use this available redirection to protect the relayer.

### Impact Explanation
This is a value-movement bug matching the "gas/fee refund to wrong address" class of the Axelar report: the relayer (functionally equivalent to `msg.sender`, the approved actor who pays) is not guaranteed to receive back a deposit that failed to be delivered, while the DelegateAction sender (functionally equivalent to the token owner in the Axelar analog) receives it instead. This creates a concrete, protocol-documented financial incentive: an untrusted or semi-trusted account can craft a `DelegateAction` whose inner actions are engineered to attach a large deposit and predictably fail on the receiver's shard (e.g. targeting a nonexistent method/receiver, or a receiver expected to reject the call), causing that deposit — paid by the relayer — to be refunded to the sender instead of returned to the relayer. This is a direct, unauthorized transfer of value from the relayer to the sender, and it is explicitly and openly documented by the nearcore team as a hazard for relayer implementations: [4](#0-3) 

Because relayer services are a general-purpose, permissionless role (any account can act as relayer for any signed `DelegateAction` sent to it off-chain), and the loss materializes purely from a single submitted meta-transaction/receipt path reachable by any user, this satisfies "unauthorized value movement" reachable from an ordinary transaction submitter.

### Likelihood Explanation
Medium. This requires a relayer that is willing to accept `SignedDelegateAction`s carrying attached deposits from a counterparty and forward them without independently verifying execution will succeed. This mirrors the judge's rationale in the original Axelar finding: severity was reduced from High to Medium because "in most cases the sender will be the one calling," but the bug remains real "in the cases where an approved address ... acts on someone else's behalf." Any production relayer service accepting deposit-bearing delegate actions from third parties (rather than only from trusted/known senders) is exposed; this is a realistic deployment scenario given NEP-366's explicit design goal of enabling third-party relayers for arbitrary users.

### Recommendation
When constructing the inner-action receipt in `apply_delegate_action`, use the `ActionReceiptV2` variant and set `refund_to` explicitly to the relayer (the original transaction signer / `action_receipt.signer_id()`), separate from `predecessor_id` (which must remain `sender_id` for permission/authorization purposes). This reuses the already-implemented `refund_to` redirection mechanism (as exercised by `test_refund_to` in `runtime/runtime/tests/test_async_calls.rs`) to ensure deposit refunds for failed inner actions go back to whichever party actually funded the deposit (the relayer), consistent with how gas refunds already correctly target the relayer via `signer_id`.

### Proof of Concept
1. Alice (untrusted sender) signs a `DelegateAction` with `sender_id = Alice`, `receiver_id = Bob`, containing an inner `FunctionCall` action to a nonexistent method on `Bob` with a nontrivial attached `deposit` (paid for by whoever wraps the transaction).
2. Relayer wraps this `SignedDelegateAction` in a transaction it signs, and submits it, prepaying the deposit and gas out of its own balance (as documented in `docs/architecture/how/meta-tx.md:44-45`, "If the inner actions have an attached token balance, this is also paid for by the relayer").
3. On the relayer's shard, `apply_delegate_action` builds `Receipt::V0(ReceiptV0 { predecessor_id: sender_id (Alice), receiver_id: Bob, ... })` (`runtime/runtime/src/actions.rs:499-513`) and forwards it to Bob's shard.
4. On Bob's shard, the inner `FunctionCall` action fails (method does not exist).
5. `refund_unspent_gas_and_deposits` generates a deposit-refund receipt to `receipt.balance_refund_receiver()`, which resolves to `predecessor_id` = Alice, since `refund_to` was never populated (`core/primitives/src/receipt.rs:416-430`).
6. Alice receives the deposit that the relayer paid for; the relayer's balance is permanently reduced by that amount with no recourse, confirming unauthorized value transfer from relayer to sender.

### Citations

**File:** runtime/runtime/src/actions.rs (L499-519)
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

    // Note, Relayer prepaid all fees and all things required by actions: attached deposits and attached gas.
    // If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction.
    // Gas is refunded to the signer, this is Relayer.
    // Some contracts refund the deposit. Usually they refund the deposit to the predecessor and this is sender_id/Sender from DelegateAction.
    // Therefore Relayer should verify DelegateAction before submitting it because it spends the attached deposit.
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

**File:** docs/architecture/how/meta-tx.md (L232-242)
```markdown
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
