### Title
Deposit Refund Misdirection in Meta Transactions: Relayer Loses Attached Deposit When Inner Action Fails — (`File: runtime/runtime/src/actions.rs`)

### Summary

The nearcore meta-transaction (NEP-366) implementation structurally detaches the party who pays for an attached deposit (the relayer) from the party who receives the deposit refund when the inner action fails (the delegate-action sender). An unprivileged user can craft a `DelegateAction` with a large attached deposit targeting a call that will deterministically fail, submit it to any relayer, and receive the full deposit refund while the relayer absorbs both the gas cost and the deposit loss.

### Finding Description

When a relayer submits a meta transaction wrapping a `DelegateAction`, `apply_delegate_action` in `runtime/runtime/src/actions.rs` constructs the inner action receipt with `predecessor_id` set to `sender_id` (the delegate-action sender, i.e., Alice), not to the relayer: [1](#0-0) 

The comment immediately below this construction acknowledges the consequence: [2](#0-1) 

When the inner receipt fails on the receiver's shard, `refund_unspent_gas_and_deposits` in `runtime/runtime/src/lib.rs` issues the deposit refund to `receipt.balance_refund_receiver()`, which resolves to the `predecessor_id` of the inner receipt — Alice — not the relayer who funded it: [3](#0-2) 

The deposit refund receipt is constructed via `Receipt::new_balance_refund`, which credits the resolved `predecessor_id`: [4](#0-3) 

Gas refunds, by contrast, go to `action_receipt.signer_id()` (the relayer), so gas is correctly returned. Only the deposit is misdirected.

The nearcore documentation explicitly acknowledges this as a live financial incentive for abuse: [5](#0-4) 

### Impact Explanation

The relayer pays for both gas and the attached deposit when submitting the meta transaction. If the inner action fails, the relayer recovers gas (via `signer_id`-routed gas refund) but loses the entire attached deposit, which is credited to Alice. The relayer's NEAR balance is permanently reduced by the deposit amount minus gas refund. This is a direct, on-chain balance loss caused by nearcore's refund routing logic, not by any off-chain component.

### Likelihood Explanation

The attack requires no special privilege. Any account can:
1. Create a `DelegateAction` with a large `deposit` on a `FunctionCall` targeting a non-existent method or a contract that will revert.
2. Forward the signed `DelegateAction` to a relayer (e.g., a public relayer service).
3. Collect the deposit refund after the inner receipt fails.

The relayer cannot atomically verify that the inner action will succeed before committing gas. The two-step structure (relayer submits → inner receipt executes on a different shard later) means there is always a window where the outcome is unknown to the relayer at submission time. The attack is repeatable and scales linearly with the deposit size.

### Recommendation

Align the deposit refund receiver with the party who funded the deposit. Two approaches:

1. **Route deposit refunds to `signer_id` (the relayer) for meta transactions.** In `apply_delegate_action`, record the relayer as the deposit-refund beneficiary on the inner receipt, overriding the default `predecessor_id`-based routing.

2. **Merge deposit payment and refund atomicity.** Require that the relayer's deposit is only deducted after the inner receipt succeeds, or provide a protocol-level escrow that returns the deposit to the relayer on failure.

At minimum, the protocol should not allow the deposit refund to flow to a party (Alice) who did not fund it, as this creates a direct, unprivileged theft vector against relayers.

### Proof of Concept

1. Alice creates a `DelegateAction` with `receiver_id = some_contract`, `actions = [FunctionCall { method_name: "nonexistent", deposit: 100_NEAR, gas: 30_TGas }]`, signs it with her key.
2. Alice submits the `SignedDelegateAction` to a relayer.
3. The relayer wraps it in a transaction (relayer is `signer_id`), paying gas + 100 NEAR deposit.
4. On Alice's shard, `apply_delegate_action` creates an inner receipt with `predecessor_id = alice`, `signer_id = relayer`.
5. On `some_contract`'s shard, the function call fails (method does not exist).
6. `refund_unspent_gas_and_deposits` issues:
   - Gas refund → `signer_id` = relayer ✓
   - Deposit refund (100 NEAR) → `balance_refund_receiver()` = `predecessor_id` = **alice** ✗
7. Alice's balance increases by 100 NEAR. Relayer's balance decreases by 100 NEAR + gas costs.

The `test-loop-tests/src/tests/gas_keys.rs` test `test_gas_key_refund` demonstrates the deposit-refund-to-account path on failure: [6](#0-5) 

The same refund routing applies to meta transactions, with the deposit going to Alice instead of the relayer.

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

**File:** runtime/runtime/src/lib.rs (L1281-1286)
```rust
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

**File:** docs/architecture/how/meta-tx.md (L236-242)
```markdown
The relayer can see what the cost will be before submitting the meta transaction
and agrees to pay for it, so nothing wrong so far. But what if the transaction
fails execution on Bob's shard? At this point, the predecessor is `Alice` and
therefore she receives the token balance refunded, not the relayer. This is
something relayer implementations must be aware of since there is a financial
incentive for Alice to submit meta transactions that have high balances attached
but will fail on Bob's shard.
```

**File:** test-loop-tests/src/tests/gas_keys.rs (L356-393)
```rust
    // Call a non-existing function on receiver (no contract deployed) with a deposit.
    // This will fail, producing both a balance refund (to account) and a gas refund (to gas key).
    let nonce_index = 0;
    let gas_key_nonce = get_gas_key_nonce(&env, sender, &gas_key_signer.public_key(), nonce_index);
    let block_hash = get_shared_block_hash(&env.node_datas, &env.test_loop.data);
    let prepaid_gas = near_primitives::types::Gas::from_teragas(100);
    let deposit_amount = Balance::from_near(5);
    let gas_key_tx = SignedTransaction::from_actions_v1(
        TransactionNonce::from_nonce_and_index(gas_key_nonce + 1, nonce_index),
        sender.clone(),
        receiver.clone(),
        &gas_key_signer,
        vec![Action::FunctionCall(Box::new(FunctionCallAction {
            method_name: "nonexistent_method".to_string(),
            args: vec![],
            gas: prepaid_gas,
            deposit: deposit_amount,
        }))],
        block_hash,
    );
    let outcome = env.rpc_runner().execute_tx(gas_key_tx, Duration::seconds(5)).unwrap();
    // Run for 1 more block for the refund to be reflected in queries.
    env.rpc_runner().run_for_number_of_blocks(1);

    // The function call should have failed (no contract on receiver).
    assert_function_call_error(&outcome);
    let tokens_burnt = total_tokens_burnt(&outcome);
    assert!(!tokens_burnt.is_zero());

    // Verify gas key balance: should be initial minus tokens_burnt (gas refund went back to gas key).
    let (_, gas_key_balance_after) =
        query_gas_key_and_balance(&env.rpc_node(), sender, &gas_key_signer.public_key());
    assert_eq!(gas_key_balance_after, gas_key_balance_before.checked_sub(tokens_burnt).unwrap());

    // Verify sender account balance is unchanged: deposit was deducted when the tx was
    // converted to a receipt, then refunded when the function call failed.
    let sender_balance_after = env.rpc_node().view_account_query(sender).unwrap().amount;
    assert_eq!(sender_balance_after, sender_balance_before);
```
