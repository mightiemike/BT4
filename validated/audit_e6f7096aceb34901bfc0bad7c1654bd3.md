## Title
Meta-transaction deposit refund is credited to the delegated sender instead of the paying relayer on inner-action failure - (File: `runtime/runtime/src/actions.rs`)

### Summary
The UXD report describes a controller that debits itself instead of the party that actually benefited from a mint, letting the beneficiary keep both the minted asset and the refunded collateral. The nearcore analog is in meta-transactions (NEP-366 `Delegate`/`DelegateV2` actions): the relayer's balance is charged for the full cost of the transaction it signs — including any deposit attached to the inner delegated action — but if that inner action fails on the receiver's shard, the deposit is refunded to the delegated sender (`sender_id`, i.e. "Alice"), not to the relayer who actually paid for it.

### Finding Description
When a relayer submits a transaction carrying a `Delegate`/`DelegateV2` action, the relayer is the `signer_id` of the outer transaction, and `verify_and_charge_tx_ephemeral` deducts the transaction's `total_cost` (which includes the deposits of all inner actions) from the relayer's account balance: [1](#0-0) 

When the delegate action is unwrapped, `apply_delegate_action` builds a brand-new receipt whose `predecessor_id` is the delegated `sender_id` (Alice), not the relayer, and does not set any `refund_to` override: [2](#0-1) 

If that new receipt's inner action (e.g. a `Transfer`) fails execution on the receiver's shard, `refund_unspent_gas_and_deposits` generates a balance-refund receipt sent to `receipt.balance_refund_receiver()`: [3](#0-2) 

`balance_refund_receiver()` falls back to `predecessor_id()` whenever `refund_to` is unset: [4](#0-3) 

Because the delegate-spawned receipt's `predecessor_id` is the sender (Alice) and `refund_to` is never populated for this path, the deposit refund lands on Alice's account — even though the relayer's balance was the one debited when the outer transaction was verified and charged. This is explicitly called out as a known risk in the architecture docs, confirming the root cause and its exploitability: [5](#0-4) 

### Impact Explanation
A malicious signer of a delegate action (Alice) can craft a meta-transaction with a large attached deposit on an inner action that is guaranteed to fail on the receiver's shard (e.g., targeting a nonexistent method, an account that will reject the deposit, or a receiver that is deleted/at capacity). A relayer who is unaware of this (or who blindly relays based on the visible pessimistic cost) pays the deposit cost out of its own balance at verification time, but upon inner-action failure the deposit is refunded to Alice instead of the relayer. This is a concrete unauthorized value transfer from the relayer to the delegated sender — the relayer's funds are effectively stolen. This exactly mirrors the UXD pattern where the "controller" (relayer) bears the debit while the "receiver" (Alice) collects the resulting refund.

### Likelihood Explanation
Reachable entirely from a submitted transaction/RPC call by an unprivileged account acting as a delegate-action sender, provided any relayer (open relayer service, aggregator, or one that doesn't carefully simulate the inner action first) signs and forwards the meta-transaction. No validator or node compromise is required — this is purely a transaction-construction attack against relayers, which is explicitly acknowledged in-repo as a real, unmitigated risk ("this is something relayer implementations must be aware of since there is a financial incentive for Alice ...").

### Recommendation
Relayer-side mitigation is documented, but the protocol itself provides no mechanism to protect an honest relayer: `DelegateAction`/`ActionReceipt` created from it has no `refund_to` field that could be set to the relayer's account. Consider:
- Adding a `refund_to` (or similar) field to `DelegateAction` that the relayer can set (and which is verified/signed as part of the payload) so that deposit refunds for delegated actions default to the actual payer (the relayer) rather than `predecessor_id`.
- Alternatively, require inner actions with non-zero attached deposits in delegate actions to be explicitly acknowledged/simulated by relayers before signing, and document/expose the relevant refund-receiver in RPC responses so relayers can programmatically detect this exposure before submitting.

### Proof of Concept
1. Alice (attacker) creates an ETH/NEAR account with no privileged access and asks any open relayer to submit a meta-transaction (`Delegate`/`DelegateV2` action) with `sender_id = Alice`, `receiver_id = Bob`, containing an inner `FunctionCall` or `Transfer` action with a large attached deposit and parameters engineered to fail on Bob's shard (e.g., a method that doesn't exist on Bob's contract, per `apply_action_receipt`'s per-action failure/rollback path).
2. The relayer signs and submits the outer transaction; `verify_and_charge_tx_ephemeral` deducts `total_cost` (including the deposit) from the relayer's balance (`runtime/runtime/src/verifier.rs:353-363`).
3. `apply_delegate_action` spawns a new receipt with `predecessor_id = sender_id` (Alice) and forwards it to Bob's shard (`runtime/runtime/src/actions.rs:499-513`).
4. The inner action fails on Bob's shard as engineered; `refund_unspent_gas_and_deposits` issues a `Receipt::new_balance_refund` to `receipt.balance_refund_receiver()`, which resolves to `predecessor_id` = Alice (`runtime/runtime/src/lib.rs:1402-1407`, `core/primitives/src/receipt.rs:416-430`).
5. Alice's account balance increases by the deposit amount that was actually paid for by the relayer, while the relayer's balance permanently decreased by that same amount — net value transfer from relayer to Alice with no compensating mechanism.

Note: this specific risk is already acknowledged in `docs/architecture/how/meta-tx.md`, indicating it's a known design tradeoff rather than a silently-introduced bug; whether the nearcore team considers it "accepted risk" for relayers versus a protocol-level gap that needs a fix is not something the available code/docs definitively resolve, and would benefit from confirmation via a live Devin session with full repository access.

### Citations

**File:** runtime/runtime/src/verifier.rs (L353-363)
```rust
    let available_balance = account.amount().saturating_sub(pending.paid_from_balance);
    if available_balance < total_cost {
        return TxVerdict::Failed(InvalidTxError::NotEnoughBalance {
            signer_id: account_id.clone(),
            balance: available_balance,
            cost: total_cost,
        });
    }
    // Debit only this tx's cost, not the pending amount (which was already
    // charged in prior chunks and will be applied at execution time).
    let new_amount = account.amount().checked_sub(total_cost).unwrap();
```

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
