## Analysis

The Cooler bug pattern is: a party who funds a payment doesn't control where a *refund* of that payment goes on failure — the protocol pushes the refund to whichever address the counterparty controls, letting that counterparty engineer a failure to redirect funds to themselves instead of back to the payer.

The same pattern exists in nearcore's meta-transaction (`DelegateAction`/`DelegateV2`) flow.

When a relayer submits a `SignedDelegateAction` on behalf of a sender, the relayer pays for any deposit attached to the inner actions [1](#0-0) . The runtime spawns a fresh receipt for the inner actions with `predecessor_id: sender_id.clone()` — i.e. the delegate's `sender_id` (Alice), not the relayer who actually paid [2](#0-1) . If that inner receipt fails, the deposit refund is generated as `Receipt::new_balance_refund(receipt.balance_refund_receiver(), deposit_refund)` [3](#0-2) , and `balance_refund_receiver()` resolves to `refund_to().unwrap_or(predecessor_id())` [4](#0-3) . Since `apply_delegate_action` builds a V1 `ActionReceipt` (no `refund_to` override), the refund always lands on the predecessor — Alice — never the relayer who funded the deposit.

This is explicitly documented as a known caveat: nearcore's own docs state "the predecessor is `Alice` and therefore she receives the token balance refunded, not the relayer... there is a financial incentive for Alice to submit meta transactions that have high balances attached but will fail on Bob's shard" [5](#0-4) .

### Title
Meta-transaction deposit refunds route to the delegate sender instead of the paying relayer, letting a malicious sender steal relayer-funded deposits via engineered failure - ([File: runtime/runtime/src/actions.rs])

### Summary
In NEP-366 meta transactions, the relayer pays gas and any attached deposit for the sender's delegated inner actions. If the inner action fails, the deposit is refunded to the `predecessor_id` of the newly spawned receipt, which is set to the delegate's `sender_id`, not the relayer who actually supplied the funds.

### Finding Description
`apply_delegate_action` constructs the inner-action receipt with `predecessor_id: sender_id.clone()` [2](#0-1) , even though the deposit attached to those actions is charged to and paid by the relayer at transaction-conversion time on the relayer's shard, not by the sender. When execution of the inner action receipt fails (e.g., the sender crafts an action that reliably fails on the target shard — calling a nonexistent method, targeting an account that will reject the action, etc.), `refund_unspent_gas_and_deposits` issues a deposit-refund receipt to `receipt.balance_refund_receiver()` [3](#0-2) , which defaults to `predecessor_id()` since no `refund_to` override is set on the V1 `ActionReceipt` used here [6](#0-5) . That predecessor is the sender (Alice), not the relayer who funded the deposit.

This is structurally identical to the Cooler bug class: the party who actually supplies the value (the relayer / lender) does not control the destination of the failure-path refund; the counterparty who can engineer the failure condition (the delegate sender / borrower-analog) captures the refund instead.

### Impact Explanation
A malicious sender can sign a `DelegateAction`/`DelegateV2` with an inner action that attaches a deposit and is guaranteed (or highly likely) to fail on the receiver's shard, then have it relayed by any relayer that doesn't perfectly simulate final on-chain conditions before submitting. The relayer's deposit is burned from the relayer's balance at tx-conversion, but on failure it is refunded to the sender rather than the relayer — unauthorized value transfer from the relayer to the malicious sender. Relayer implementations are explicitly warned about this in nearcore's own docs, confirming it is a real, exploitable fund-diversion path, not a purely theoretical concern.

### Likelihood Explanation
Exploitability requires a relayer to accept and submit an attacker-supplied `SignedDelegateAction` without fully re-validating that the inner action will succeed against current receiver-shard state — a realistic operational gap for permissionless or naively-implemented relayer services, since relayers are meant to serve senders they don't necessarily trust (that's the entire point of meta-transactions/NEP-366).

### Recommendation
Route deposit refunds for delegated (meta-transaction) inner actions to the relayer (the actual predecessor of the outer `DelegateAction` / the receipt signer) rather than to `sender_id`, e.g. by populating `refund_to` on the generated inner receipt (using the `ActionReceiptV2`/`refund_to` mechanism already available in the codebase, as demonstrated by `promise_refund_to` [7](#0-6) ) so failure-path deposit refunds cannot be captured by the delegate sender.

### Proof of Concept
1. Attacker (Alice) signs a `DelegateActionV2` targeting `receiver_id = Bob` with an inner `FunctionCall` action carrying a large deposit and a method name known not to exist on Bob's contract (guaranteed `MethodNotFound`/execution failure).
2. Alice sends the `SignedDelegateAction` off-chain to Relayer, who wraps it in a transaction and submits it, paying the attached deposit + gas out of the relayer's own account (as in `apply_delegate_action`, `runtime/runtime/src/actions.rs:515-519`).
3. On Bob's shard the inner receipt fails; `refund_unspent_gas_and_deposits` issues a `Receipt::new_balance_refund` to `receipt.balance_refund_receiver()`, which equals `predecessor_id()` = Alice's `sender_id` (`core/primitives/src/receipt.rs:428-430`, `runtime/runtime/src/lib.rs:1402-1407`).
4. Alice receives the deposit the relayer paid for; the relayer's funds are gone with nothing to show for it — reproducible per the caveat already documented in `docs/architecture/how/meta-tx.md:232-242`.

### Citations

**File:** runtime/runtime/src/actions.rs (L499-513)
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

**File:** runtime/runtime/src/actions.rs (L515-519)
```rust
    // Note, Relayer prepaid all fees and all things required by actions: attached deposits and attached gas.
    // If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction.
    // Gas is refunded to the signer, this is Relayer.
    // Some contracts refund the deposit. Usually they refund the deposit to the predecessor and this is sender_id/Sender from DelegateAction.
    // Therefore Relayer should verify DelegateAction before submitting it because it spends the attached deposit.
```

**File:** runtime/runtime/src/lib.rs (L1402-1407)
```rust
        if deposit_refund > Balance::ZERO {
            result.new_receipts.push(Receipt::new_balance_refund(
                receipt.balance_refund_receiver(),
                deposit_refund,
            ));
        }
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

**File:** runtime/runtime/tests/test_async_calls.rs (L1204-1235)
```rust
// redirect the balance refund using `promise_refund_to`
#[test]
fn test_refund_to() {
    let group = RuntimeGroup::new(4, 4, near_test_contracts::rs_contract());

    let signer_sender = group.signers[0].clone();
    let signer_receiver = group.signers[1].clone();
    let deposit = Balance::from_yoctonear(1000);

    let data = serde_json::json!([
        {
            "batch_create": {
                "account_id": "near_2",
            },
            "id": 0
        },
        {
            "action_function_call": {
                "promise_index": 0,
                "method_name": "non_existing_function",
                "arguments": [],
                "amount": deposit,
                "gas": GAS_2,
            },
            "id": 0
        },
        {
            "set_refund_to": {
                "promise_index": 0,
                "beneficiary_id": "near_3"
            }, "id": 0
        }
```
