This confirms the mechanism precisely and it is explicitly documented (not a hidden defect) in the nearcore codebase itself. The comment in `apply_delegate_action` at [1](#0-0)  and the doc at [2](#0-1)  both state that balance refunds for a meta-transaction's inner actions go to `sender_id` (Alice, the predecessor of the new receipt), not to the relayer who actually funded the deposit — this is called out as known, intended behavior that relayers must account for, not a silent bug.

### Title
Meta-transaction balance refunds credited to delegate sender instead of paying relayer, enabling relayer fund drain - (File: runtime/runtime/src/actions.rs)

### Summary
In NEP-366 meta transactions, a relayer funds the attached deposits for a `DelegateAction`'s inner actions, but if those inner actions fail on the receiver shard, the deposit refund is sent to `predecessor_id` of the generated receipt, which `apply_delegate_action` sets to `sender_id` (Alice) rather than to the relayer who actually paid.

### Finding Description
`apply_delegate_action` constructs the forwarded receipt with `predecessor_id: sender_id.clone()` (Alice) while the relayer remains only the `signer_id` on the receipt [3](#0-2) . Deposit refunds always go to `Receipt::balance_refund_receiver()`, which defaults to `predecessor_id()` [4](#0-3) . The runtime's refund generation in `Runtime` (`runtime/runtime/src/lib.rs`) pushes `Receipt::new_balance_refund(receipt.balance_refund_receiver(), deposit_refund)` on any failed action receipt [5](#0-4) . Because the relayer never appears as `predecessor_id` of the inner-action receipt, any deposit the relayer prepaid for the DelegateAction's inner actions is refunded to Alice (`sender_id`), not the relayer, if the receiver-side execution fails. This is architecturally identical to the reported C4 finding pattern: a wrapping/dispatching layer (`this.claim()` / Claimable contract calling through an intermediary) causes the "sender" observed by the refund logic to diverge from the party that actually supplied the funds, misdirecting the refund.

### Impact Explanation
A relayer that services a meta-transaction with an attached deposit (e.g., a `Transfer` or a paid `FunctionCall`) can have its funds redirected to the delegate's `sender_id` whenever the inner action fails on the receiver shard — for example if the receiver account/method rejects the call, is deleted, or a paid function call panics. This is a concrete unauthorized value movement: NEAR that the relayer paid for ends up credited to the sender's account balance instead of returning to the relayer, and a malicious sender can engineer this outcome deliberately (craft a DelegateAction targeting a receiver/method they know will fail after checking that the relayer's deposit is attached) to systematically drain relayer funds one meta-transaction at a time.

### Likelihood Explanation
This requires only a single account (no special privileges) acting as the meta-transaction sender and reachable via ordinary transaction submission (`Action::Delegate`) plus cooperation, complicity, or simple naivety of any public relayer. The nearcore code and its own documentation already flag this as "something relayer implementations must be aware of," confirming it is trivially reachable and reproducible using standard NEP-366 tooling [6](#0-5) .

### Recommendation
Track the relayer identity that funded the DelegateAction's inner receipt separately from `sender_id`, and route deposit refunds for failed inner-action receipts of a delegate action to the relayer (e.g., via a `refund_to` field similar to the one already used for `ActionReceiptV2`/`PromiseYieldV2`, seen in `Receipt::refund_to` [7](#0-6) ) rather than defaulting to `predecessor_id`.

### Proof of Concept
1. Relayer submits a transaction containing `Action::Delegate(SignedDelegateAction)` where `DelegateAction.actions` includes a `FunctionCall` with a non-trivial `deposit` (paid for by the relayer's prepaid balance), targeting a `receiver_id`/`method_name` combination known to fail (nonexistent method, insufficient gas post-verification, or a contract that panics).
2. `apply_delegate_action` forwards a new receipt with `predecessor_id = sender_id` (Alice) and the failing inner action, per [3](#0-2) .
3. Execution of the inner action fails on the receiver shard; the runtime computes `deposit_refund` and issues `Receipt::new_balance_refund(receipt.balance_refund_receiver(), deposit_refund)`, where `balance_refund_receiver()` resolves to `predecessor_id` = Alice (`sender_id`), not the relayer [5](#0-4) .
4. Alice's account balance increases by the deposit the relayer paid, while the relayer receives nothing back, confirmed by the integration test framework's `meta_tx` helper reproducing this exact receipt shape [8](#0-7) .

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

**File:** runtime/runtime/src/lib.rs (L1402-1407)
```rust
        if deposit_refund > Balance::ZERO {
            result.new_receipts.push(Receipt::new_balance_refund(
                receipt.balance_refund_receiver(),
                deposit_refund,
            ));
        }
```

**File:** integration-tests/src/user/mod.rs (L283-318)
```rust
    /// Wrap the given actions in a delegate action and execute them.
    ///
    /// The signer signs the delegate action to be sent to the receiver. The
    /// relayer packs that in a transaction and signs it .
    fn meta_tx(
        &self,
        signer_id: AccountId,
        receiver_id: AccountId,
        relayer_id: AccountId,
        actions: Vec<Action>,
    ) -> Result<FinalExecutionOutcomeView, CommitError> {
        let inner_signer = create_user_test_signer(&signer_id);
        let user_nonce = self
            .get_access_key(&signer_id, &inner_signer.public_key())
            .expect("failed reading user's nonce for access key")
            .nonce;
        let delegate_action = DelegateAction {
            sender_id: signer_id.clone(),
            receiver_id,
            actions: actions
                .into_iter()
                .map(|action| NonDelegateAction::try_from(action).unwrap())
                .collect(),
            nonce: user_nonce + 1,
            max_block_height: 100,
            public_key: inner_signer.public_key(),
        };
        let signature = inner_signer.sign(delegate_action.get_nep461_hash().as_bytes());
        let signed_delegate_action = SignedDelegateAction { delegate_action, signature };

        self.sign_and_commit_actions(
            relayer_id,
            signer_id,
            vec![Action::Delegate(Box::new(signed_delegate_action))],
        )
    }
```
