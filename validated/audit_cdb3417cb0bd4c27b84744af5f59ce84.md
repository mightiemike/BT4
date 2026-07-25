### Title
Deposit Refund in Meta-Transactions Sent to Sender (Alice) Instead of Relayer Who Funded the Deposit — (`runtime/runtime/src/actions.rs`)

### Summary

When a meta-transaction (`DelegateAction`) inner receipt fails on the receiver's shard, the attached deposit is refunded to the `sender_id` (Alice, the intermediary signer), not to the relayer who actually paid for it. An unprivileged user (Alice) can deliberately craft a meta-transaction with a large deposit that will fail, causing the relayer to permanently lose the deposit while Alice receives it.

### Finding Description

`apply_delegate_action` in `runtime/runtime/src/actions.rs` constructs the inner receipt that carries the delegated actions to the final receiver (Bob):

```rust
let new_receipt = Receipt::V0(ReceiptV0 {
    predecessor_id: sender_id.clone(),   // ← Alice
    receiver_id: delegate_action.receiver_id().clone(),
    ...
    receipt: ReceiptEnum::Action(ActionReceipt { ... }),
});
``` [1](#0-0) 

The `predecessor_id` of this inner receipt is set to `sender_id` (Alice). When the inner receipt fails on Bob's shard, `refund_unspent_gas_and_deposits` issues a deposit-refund receipt to `receipt.balance_refund_receiver()`:

```rust
if deposit_refund > Balance::ZERO {
    result.new_receipts.push(Receipt::new_balance_refund(
        receipt.balance_refund_receiver(),
        deposit_refund,
    ));
}
``` [2](#0-1) 

`balance_refund_receiver()` returns `refund_to` if set, otherwise `predecessor_id()`:

```rust
pub fn balance_refund_receiver(&self) -> &AccountId {
    self.refund_to().as_ref().unwrap_or_else(|| self.predecessor_id())
}
``` [3](#0-2) 

Because the inner receipt is `Receipt::V0` / `ReceiptEnum::Action` (not `ActionReceiptV2`), `refund_to` is always `None`:

```rust
ReceiptEnum::Action(_) | ... => &None,
ReceiptEnum::ActionV2(action_receipt_v2) => &action_receipt_v2.refund_to,
``` [4](#0-3) 

So the deposit refund always goes to `predecessor_id` = Alice, not to the relayer. The code comment in `apply_delegate_action` explicitly acknowledges this:

> "If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction. … Therefore Relayer should verify DelegateAction before submitting it because it spends the attached deposit." [5](#0-4) 

The architecture documentation also acknowledges the financial incentive:

> "At this point, the predecessor is Alice and therefore she receives the token balance refunded, not the relayer. This is something relayer implementations must be aware of since there is a financial incentive for Alice to submit meta transactions that have high balances attached but will fail on Bob's shard." [6](#0-5) 

There is no code-level guard preventing Alice from exploiting this. The only guard is an operational recommendation to relayers.

### Impact Explanation

The relayer pays for all deposits attached to inner actions in a meta-transaction. If the inner receipt fails on the receiver's shard, the deposit is refunded to Alice (the sender), not the relayer. Alice can deliberately craft a meta-transaction with a large deposit targeting a receiver that will reject it (e.g., a non-existent method on a contract, or an account with no contract), causing the relayer to permanently lose the deposit while Alice receives it. The loss is proportional to the deposit size, which is unbounded.

### Likelihood Explanation

Any user (Alice) who can get a relayer to submit a meta-transaction on their behalf can trigger this. The attack requires only:
1. Crafting a `DelegateAction` with a large deposit attached to an inner `FunctionCall`
2. Targeting a receiver that will reject the call (trivially achievable)
3. Getting the relayer to submit it (relayers are designed to accept user-submitted delegate actions)

No privileged access is required. The attack is deterministic and repeatable.

### Recommendation

The inner receipt created by `apply_delegate_action` should set `refund_to` to the relayer's account ID (the `signer_id` of the outer action receipt) so that deposit refunds are directed to the party who funded them. This requires using `ReceiptEnum::ActionV2` with `refund_to: Some(action_receipt.signer_id().clone())` instead of `ReceiptEnum::Action`. The `refund_to` field in `ActionReceiptV2` was introduced precisely for this purpose. [7](#0-6) 

### Proof of Concept

1. Alice creates a `DelegateAction` with `sender_id = alice`, `receiver_id = bob`, inner action = `FunctionCall { method_name: "nonexistent", deposit: 10 NEAR, gas: 100 TGas }`.
2. Alice signs the `DelegateAction` and gives it to a relayer.
3. Relayer wraps it in a transaction and submits it. The relayer's account is charged 10 NEAR for the deposit.
4. On Alice's shard, `apply_delegate_action` creates an inner receipt with `predecessor_id = alice`, `receiver_id = bob`.
5. On Bob's shard, the function call fails (no contract / method not found).
6. `refund_unspent_gas_and_deposits` issues a deposit refund of 10 NEAR to `receipt.balance_refund_receiver()` = `predecessor_id` = Alice.
7. Alice receives 10 NEAR. The relayer loses 10 NEAR.

This is confirmed by the integration test `test_gas_key_refund` which shows deposit refunds going to the sender account when a function call fails, and by the explicit documentation acknowledgment of the financial incentive. [8](#0-7)

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

**File:** core/primitives/src/receipt.rs (L416-426)
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

**File:** docs/architecture/how/meta-tx.md (L237-242)
```markdown
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
