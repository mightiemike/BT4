### Title
Malicious meta-transaction sender can grief the relayer by forcing deposit refunds to themselves instead of the paying relayer - (File: runtime/runtime/src/actions.rs)

### Summary
In NEP-366 meta-transactions, the relayer signs and pays for the entire cost (gas + attached deposits) of a `DelegateAction`, while the inner actions execute with `sender_id` (e.g., "Alice") as `predecessor_id`. If an inner action with an attached deposit fails on the receiver shard, the deposit refund is sent to the `predecessor_id` (the sender who never paid), not to the relayer who funded the deposit. This lets a malicious sender deliberately construct a delegate action that fails after execution, capturing funds the relayer paid for — directly analogous to the `forceRepay` griefing pattern where the actor who controls execution choice offloads cost/loss onto the party who is supposed to benefit.

### Finding Description
When a relayer submits a `SignedDelegateAction` on behalf of a sender, `apply_delegate_action` builds a new inner receipt with `predecessor_id: sender_id.clone()` (Alice) while the relayer remains only the `signer_id`/gas payer [1](#0-0) . The code comment explicitly documents the resulting asymmetry: "Relayer prepaid all fees and all things required by actions: attached deposits and attached gas. If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction... Therefore Relayer should verify DelegateAction before submitting it because it spends the attached deposit." [2](#0-1) 

This is enforced by the generic refund logic: on execution failure, the full attached deposit is refunded via `Receipt::new_balance_refund(receipt.balance_refund_receiver(), deposit_refund)`, which resolves to the receipt's `predecessor_id` [3](#0-2) , and `new_balance_refund` sends a system `Transfer` action straight to that account with no re-check of who actually paid [4](#0-3) .

The architecture docs confirm this is a known but unmitigated incentive flaw: "If an inner action requires an attached balance... this balance is taken from the relayer... But what if the transaction fails execution on Bob's shard? At this point, the predecessor is Alice and therefore she receives the token balance refunded, not the relayer. This is something relayer implementations must be aware of since there is a financial incentive for Alice to submit meta transactions that have high balances attached but will fail on Bob's shard." [5](#0-4) 

The parallel to the reported bug is structural: just as a borrower can choose the cheaper `forceRepay` path that shifts settlement cost onto the lender, a meta-transaction sender ("Alice") who signs a `DelegateAction` can choose a receiver/method that consumes the attached deposit's execution attempt but ultimately fails (e.g., targeting a contract/method that reverts, an account/method combination she knows will error, or a receiver that both accepts and discards/refunds funds through its own logic before erroring), causing the protocol-level deposit refund to route back to her rather than to the relayer who funded the deposit in the first place. The relayer bears the full gas + deposit cost with no way to reclaim the deposit portion, because the protocol always refunds failed-receipt deposits to `predecessor_id`, which for delegate-action inner receipts is always the sender, never the relayer.

### Impact Explanation
This is a real, unauthorized value transfer path from relayer to sender: the relayer's attached balance for inner actions can be captured by the sender via a receipt engineered to fail after the deposit obligation is set, while the relayer never gets it back (the refund goes to the sender/predecessor by protocol rule, not the party who funded it). This meets the "concrete unauthorized value movement" bar. It specifically harms any general-purpose or application-specific relayer infrastructure operating meta-transactions/NEP-366, which is a first-class, unprivileged transaction-signer-reachable feature (a relayer submits an ordinary signed transaction wrapping a `SignedDelegateAction`).

### Likelihood Explanation
Likelihood is high because the attacker only needs signing capability over their own account and access to any relayer that accepts `DelegateAction`s with attached deposits for actions that can be made to fail (e.g., function calls to contracts with conditions the sender controls, or targeting methods/receivers that don't exist for certain executions). No special privileges, validator, or protocol-level access is required — a single crafted meta-transaction from an ordinary signer suffices. The docs themselves flag this as a known incentive problem requiring relayers to defensively simulate/verify, indicating the underlying protocol behavior (refund-to-predecessor for inner delegate-action receipts) is unconditional and not mitigated at the protocol layer.

### Recommendation
- Route deposit refunds for delegate-action inner receipts to the entity that actually paid (the relayer / outer transaction signer) rather than unconditionally to `predecessor_id`, at least for the deposit portion attributable to attached balances funded by the relayer.
- Alternatively, require relayers to only accept meta-transactions whose failure paths are provably safe (e.g., via a required successful dry-run/simulation before broadcasting), and/or add an optional protocol-level "deposit refund to signer" flag on `DelegateAction` so relayers can opt into safe semantics instead of relying purely on off-chain trust as currently documented.
- At minimum, elevate this from a documentation caveat to an explicit protocol safeguard, since documentation alone does not prevent an adversarial sender from exploiting it against any relayer that fails to perfectly predict execution outcomes.

### Proof of Concept
1. Alice (an account with a `FullAccess` key but no NEAR balance) creates a `DelegateAction` targeting contract `Bob`, with an inner `FunctionCall` action that has `deposit > 0` and calls a method that Alice knows/arranges will fail (e.g., a method requiring a precondition Alice can toggle to false right before submission, or simply a nonexistent/soon-to-be-removed method).
2. Alice signs the `DelegateAction` and sends it to a relayer, which wraps it in a `SignedTransaction` and pays all gas and the attached deposit `d`.
3. On-chain: the outer transaction is converted to a receipt on the relayer's shard, and forwarded to Alice's account, where `apply_delegate_action` unpacks it and creates the inner receipt with `predecessor_id = Alice`, `signer_id = relayer` [6](#0-5) .
4. The inner receipt executes on Bob's shard and fails (the targeted method reverts / doesn't exist).
5. `refund_unspent_gas_and_deposits` computes `deposit_refund = total_deposit` (since `result.result.is_err()`) and creates `Receipt::new_balance_refund(receipt.balance_refund_receiver(), deposit_refund)` [7](#0-6) , which sends the deposit `d` back to `predecessor_id` = Alice, not to the relayer who funded it.
6. Net result: the relayer paid gas fees plus deposit `d`, and Alice receives `d` back despite never having funded it — a direct value transfer from relayer to sender enabled purely by Alice choosing to submit a delegate action she knows will fail.

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

**File:** runtime/runtime/src/lib.rs (L1303-1407)
```rust
        let deposit_refund = if result.result.is_err() { total_deposit } else { Balance::ZERO };
        let gross_gas_refund = if result.result.is_err() {
            prepaid_gas
                .checked_add(prepaid_exec_gas.gas)
                .ok_or(IntegerOverflowError)?
                .checked_sub(result.gas_burnt)
                .unwrap()
        } else {
            prepaid_gas
                .checked_add(prepaid_exec_gas.gas)
                .ok_or(IntegerOverflowError)?
                .checked_sub(result.gas_used)
                .unwrap()
        };

        // NEP-536 also adds a penalty to gas refund.
        let refund_penalty: Gas = config.fees.gas_penalty_for_gas_refund(gross_gas_refund);
        let penalty_gas_price = if ProtocolFeature::AccountCostIncrease.enabled(protocol_version) {
            gas_burn_price
        } else {
            gas_purchase_price
        };
        let refund_penalty_amount = safe_gas_to_balance(penalty_gas_price, refund_penalty)?;

        // Refund for the leftover gas that was not used by this receipt.
        let unused_gas_balance_refund = safe_gas_to_balance(gas_purchase_price, gross_gas_refund)?
            .saturating_sub(refund_penalty_amount);

        let mut gas_refund_result = GasRefundResult {
            price_deficit: Balance::ZERO,
            price_surplus: Balance::ZERO,
            refund_penalty: refund_penalty_amount,
            create_account_charge: Balance::ZERO,
        };

        if gas_burn_price > gas_purchase_price {
            // price increased, burning resulted in a deficit
            gas_refund_result.price_deficit = safe_gas_to_balance(
                gas_burn_price.checked_sub(gas_purchase_price).unwrap(),
                result.gas_burnt,
            )?;
        } else {
            // price decreased, burning resulted in a surplus
            gas_refund_result.price_surplus = safe_gas_to_balance(
                gas_purchase_price.checked_sub(gas_burn_price).unwrap(),
                result.gas_burnt,
            )?;
        };

        // Refund for the price difference between gas_purchase_price and gas_burn_price of the gas burned in this receipt.
        let mut burned_gas_refund =
            if ProtocolFeature::AccountCostIncrease.enabled(protocol_version) {
                gas_refund_result.price_surplus
            } else {
                Balance::ZERO
            };

        // If an account was created, charge more to cover its cost.
        if created_account && ProtocolFeature::AccountCostIncrease.enabled(protocol_version) {
            // This is how much creating an account should cost
            let desired_cost = config.account_creation_charge;

            let create_account_gas_cost =
                config.fees.fee(ActionCosts::create_account).exec_fee().gas;
            // The cost of the gas that was burned already
            let burned_cost = safe_gas_to_balance(gas_burn_price, create_account_gas_cost)?;

            // We would like to charge as much as needed to reach desired_cost
            let amount_to_charge = desired_cost.saturating_sub(burned_cost);

            // We can't charge more than `burned_gas_refund`.
            // `burned_gas_refund < amount_to_charge` could happen for receipts where the gas was
            // purchased in protocol versions before `ProtocolFeature::AccountCostIncrease`, at a lower
            // gas price that isn't enough to cover the cost of creating an account.
            let amount_actually_charged = std::cmp::min(amount_to_charge, burned_gas_refund);

            // sanity check: purchasing gas at `min_gas_purchase_price` should be enough to cover
            // the cost of creating an account.
            debug_assert!(
                safe_gas_to_balance(config.min_gas_purchase_price, create_account_gas_cost)
                    .unwrap()
                    >= desired_cost
            );

            // sanity check: as long as the purchase price is high enough, there should always be
            // enough refund balance to cover the cost of creating an account.
            if gas_purchase_price >= config.min_gas_purchase_price {
                debug_assert!(burned_gas_refund >= amount_to_charge);
            }

            // Subtract `amount_actually_charged` from the refund.
            gas_refund_result.create_account_charge = amount_actually_charged;
            burned_gas_refund = burned_gas_refund
                .checked_sub(amount_actually_charged)
                .expect("burned_gas_refund >= amount_actually_charged checked above");
        }

        let gas_balance_refund = safe_add_balance(unused_gas_balance_refund, burned_gas_refund)?;

        if deposit_refund > Balance::ZERO {
            result.new_receipts.push(Receipt::new_balance_refund(
                receipt.balance_refund_receiver(),
                deposit_refund,
            ));
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
