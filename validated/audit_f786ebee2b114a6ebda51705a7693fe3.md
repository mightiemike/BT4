### Title
Meta-transaction sender can grief the relayer by forcing a failed inner receipt so its deposit refund is stolen instead of returned to the payer - ([File: runtime/runtime/src/actions.rs])

### Summary
The Cooler `setDirectRepay` bug lets a privileged party (the lender) choose, at will, the destination of a value-transfer step that another party (the borrower) does not control, so that the destination-picker can weaponize a normal failure path (a blacklist revert) to divert or block funds belonging to someone else. The same value-routing/failure-diversion pattern exists in nearcore's meta-transaction (`DelegateAction`) balance-refund mechanics: the party who does **not** pay for the attached deposit (`sender_id`/Alice) is the one who receives the refund if the inner receipt fails, while the party who actually pays (the relayer) has no control over that outcome and no way to reclaim it.

### Finding Description
When a relayer submits a meta-transaction wrapping Alice's `SignedDelegateAction`, `apply_delegate_action` (`runtime/runtime/src/actions.rs:499-532`) builds a brand-new action receipt whose `predecessor_id` is `sender_id` (Alice), even though the relayer is the one who purchased gas and prepaid any attached deposit for the inner actions [1](#0-0) . The code's own comment makes the design explicit: "Relayer prepaid all fees and all things required by actions: attached deposits and attached gas. If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction. Gas is refunded to the signer, this is Relayer." [2](#0-1) 

Refund routing is enforced later at receipt-execution failure time by `refund_unspent_gas_and_deposits`, which sends the deposit refund to `receipt.balance_refund_receiver()` (the predecessor of the failed receipt) via `Receipt::new_balance_refund`, while the gas refund correctly goes to `action_receipt.signer_id()` (the relayer, the original transaction signer) [3](#0-2) [4](#0-3) .

This split is documented as a known caveat in `docs/architecture/how/meta-tx.md`: "The relayer can see what the cost will be before submitting the meta transaction and agrees to pay for it... But what if the transaction fails execution on Bob's shard? At this point, the predecessor is Alice and therefore she receives the token balance refunded, not the relayer. This is something relayer implementations must be aware of since there is a financial incentive for Alice to submit meta transactions that have high balances attached but will fail on Bob's shard." [5](#0-4) 

Structurally this is the same bug class as the Cooler report: a party who does not fund the value (Alice, analogous to the lender who does not lose money either way) can deterministically force a "failure" outcome (analogous to the blacklist revert) on an action whose deposit was paid by a different, unprivileged counterparty (the relayer, analogous to the borrower who cannot control the outcome), and by doing so redirect that value to themselves rather than to its rightful owner.

### Impact Explanation
Alice, an ordinary unprivileged account, can craft a `DelegateAction` whose inner action is guaranteed or highly likely to fail on the receiver's shard (e.g., targeting a nonexistent method, a contract that reverts on certain inputs, or a receiver Alice controls and can make revert), while asking a relayer to attach a token deposit on her behalf. When the inner receipt fails, the attached deposit — funded entirely by the relayer — is refunded to Alice (the predecessor of that receipt) instead of the relayer who paid for it. This is concrete unauthorized value movement from the relayer to the delegate-action sender, reachable by a single submitted transaction (the relayer's tx wrapping Alice's signed `DelegateAction`), with no additional privilege required from Alice.

### Likelihood Explanation
This does not require any protocol-level exploit or corner case — it is an inherent consequence of how `apply_delegate_action` sets `predecessor_id = sender_id` on the newly generated receipt while the relayer funds it [6](#0-5) , and it is explicitly called out as a known financial-incentive problem in the project's own architecture docs [7](#0-6) . Any relayer that accepts meta-transactions with non-trivial attached deposits without additional trust assumptions or safeguards on the sender is exposed. The only mitigation currently is social/trust-based ("some trust is required"), not a protocol guarantee.

### Recommendation
Route balance refunds for delegate-action-spawned receipts to the actual funder of the deposit (the relayer/original transaction signer) rather than unconditionally to `predecessor_id`, mirroring how gas refunds already go to the signer. Alternatively, require relayers to pre-simulate/validate that the inner action cannot trivially fail before funding the deposit, or expose an explicit "refund destination" concept (similar to `promise_set_refund_to`) that lets the actual depositor, not the delegate-action sender, be reimbursed on failure.

### Proof of Concept
1. Relayer receives a `SignedDelegateAction` from Alice whose single inner action is a `FunctionCall` to a receiver/method Alice knows will fail (e.g., a non-existent method, or a contract Alice controls that reverts based on input) and that carries a nontrivial attached deposit.
2. Relayer wraps it in a transaction and submits it, paying gas and, since the inner action requires it, the deposit — per `apply_delegate_action`, the resulting receipt's `predecessor_id` is Alice (`sender_id`), not the relayer [6](#0-5) .
3. The inner receipt executes on Bob's shard and fails as intended.
4. `refund_unspent_gas_and_deposits` generates a `new_balance_refund` receipt to `receipt.balance_refund_receiver()`, i.e., Alice, for the full deposit amount that the relayer paid [8](#0-7) [4](#0-3) .
5. Alice's account balance increases by the deposit amount while the relayer's balance decreased by that same amount and receives nothing back — repeatable at will against any relayer that funds deposits for meta-transactions.

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

**File:** runtime/runtime/src/lib.rs (L1400-1419)
```rust
        let gas_balance_refund = safe_add_balance(unused_gas_balance_refund, burned_gas_refund)?;

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

        Ok(gas_refund_result)
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
