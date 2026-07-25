### Title
Deposit Refund Misdirection in Meta-Transactions Allows Sender to Steal Relayer's Attached Deposit — (`runtime/runtime/src/actions.rs`)

### Summary

When a `DelegateAction` (meta-transaction) contains an inner `FunctionCall` with an attached deposit, the relayer pays that deposit at transaction-conversion time. However, `apply_delegate_action` sets the inner receipt's `predecessor_id` to the **sender** (Alice), not the relayer. If the inner receipt fails on the receiver's shard, the protocol refunds the deposit to `predecessor_id` — Alice — not to the relayer who actually paid. An unprivileged user can deliberately craft a `DelegateAction` whose inner call is guaranteed to fail, causing the relayer to lose the full attached deposit while the sender gains it. This is exploitable by any ordinary NEAR account with no privileged access.

### Finding Description

**Root cause — `apply_delegate_action`, `runtime/runtime/src/actions.rs:456-469`**

```rust
let new_receipt = Receipt::V0(ReceiptV0 {
    predecessor_id: sender_id.clone(),   // ← Alice, not the relayer
    receiver_id: delegate_action.receiver_id().clone(),
    ...
    receipt: ReceiptEnum::Action(ActionReceipt {
        signer_id: action_receipt.signer_id().clone(), // relayer
        ...
        actions: delegate_action.get_actions(),
    }),
});
``` [1](#0-0) 

The relayer is the `signer_id` of the outer receipt (and therefore pays all costs including the attached deposit), but the inner receipt's `predecessor_id` is set to `sender_id` (Alice).

**Refund routing — `refund_unspent_gas_and_deposits`, `runtime/runtime/src/lib.rs:1281-1285`**

```rust
if deposit_refund > Balance::ZERO {
    result.new_receipts.push(Receipt::new_balance_refund(
        receipt.balance_refund_receiver(),  // ← predecessor_id = Alice
        deposit_refund,
    ));
}
``` [2](#0-1) 

`balance_refund_receiver()` returns `predecessor_id` for a V0 receipt:

```rust
pub fn balance_refund_receiver(&self) -> &AccountId {
    self.refund_to().as_ref().unwrap_or_else(|| self.predecessor_id())
}
``` [3](#0-2) 

For a V0 receipt (which `apply_delegate_action` always creates), `refund_to()` returns `&None`, so the fallback is `predecessor_id` = Alice. [4](#0-3) 

**Exploit flow:**

1. Alice (unprivileged) creates a `DelegateAction` targeting a contract method she knows will fail (e.g., a non-existent method, or a method that panics), with a large `deposit` attached to the inner `FunctionCall`.
2. Alice signs the `DelegateAction` with her access key and sends it off-chain to a relayer.
3. The relayer wraps it in a `SignedTransaction` and submits it. At transaction conversion, the relayer's account is charged the full deposit amount.
4. On Alice's shard, `apply_delegate_action` validates the signature and nonce, then emits the inner receipt with `predecessor_id = Alice`.
5. On the receiver's shard, the function call fails. `refund_unspent_gas_and_deposits` emits a `Receipt::new_balance_refund` to `predecessor_id` = Alice.
6. Alice's balance increases by the full deposit. The relayer's balance is permanently reduced by that amount.

The nearcore documentation explicitly acknowledges this behavior:

> "there is a financial incentive for Alice to submit meta transactions that have high balances attached but will fail on Bob's shard." [5](#0-4) 

The comment in `apply_delegate_action` also acknowledges it:

> "If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction." [6](#0-5) 

### Impact Explanation

An unprivileged NEAR account can repeatedly drain NEAR tokens from any relayer that processes their `DelegateAction`s. Each exploit iteration steals exactly the deposit amount attached to the inner `FunctionCall`. With a deposit of, say, 10 NEAR per transaction, and a relayer processing many requests, the attacker can drain the relayer's balance in proportion to the number of accepted meta-transactions. This is a direct, protocol-level fund-stealing path reachable from ordinary user actions with no privileged keys required.

The broken invariant is: **the party that pays a deposit must receive the refund if the action fails**. The relayer pays the deposit (charged at `verify_and_charge_transaction` time from the relayer's account), but the refund is routed to Alice.

### Likelihood Explanation

Any user who can find a relayer willing to forward their `DelegateAction` can trigger this. Public relayer services (which are the intended use case for NEP-366) are the primary target. The attacker needs only a valid NEAR account and the ability to craft a `DelegateAction` whose inner call will fail — trivially achievable by targeting a non-existent method or a contract that panics on the given input. No key compromise, no privileged access, and no social engineering beyond submitting a signed `DelegateAction` to a relayer is required.

### Recommendation

1. **Route deposit refunds to the relayer (signer) rather than the sender (predecessor) for meta-transactions.** In `apply_delegate_action`, use `ReceiptEnum::ActionV2` (which supports an explicit `refund_to` field) and set `refund_to = Some(action_receipt.signer_id().clone())` (the relayer) so that deposit refunds from failed inner receipts return to the party that paid them.

2. **Alternatively**, require relayers to verify that inner `FunctionCall` deposits are zero, or that the target contract and method are known to succeed, before submitting. This is an off-chain mitigation but does not fix the protocol-level invariant.

3. **Document the invariant explicitly** in the protocol spec and add a test that asserts deposit refunds from failed meta-transaction inner receipts go to the relayer, not the sender.

### Proof of Concept

```
Alice account: alice.near  (attacker, no privileged keys)
Relayer account: relayer.near  (victim, has NEAR balance)
Target: nonexistent.near  (does not exist, any call will fail)

1. Alice creates:
   DelegateAction {
     sender_id: "alice.near",
     receiver_id: "nonexistent.near",
     actions: [FunctionCall { method_name: "drain", deposit: 100_000_000_000_000_000_000_000_000 /* 100 NEAR */, gas: 10_000_000_000_000 }],
     nonce: alice_nonce + 1,
     max_block_height: current_height + 100,
     public_key: alice_full_access_key,
   }
   Signs it with alice_full_access_key.

2. Alice sends the SignedDelegateAction to relayer.near off-chain.

3. Relayer wraps it:
   SignedTransaction {
     signer_id: "relayer.near",
     receiver_id: "alice.near",   // must match sender_id
     actions: [Delegate(signed_delegate_action)],
     ...
   }
   Relayer's account is charged 100 NEAR deposit + gas at tx conversion.

4. On alice.near's shard: apply_delegate_action runs.
   Inner receipt emitted: predecessor_id = "alice.near", signer_id = "relayer.near"

5. On nonexistent.near's shard: account does not exist → AccountDoesNotExist error.
   refund_unspent_gas_and_deposits:
     deposit_refund = 100 NEAR
     Receipt::new_balance_refund("alice.near", 100 NEAR)  ← predecessor_id

6. alice.near receives 100 NEAR. relayer.near lost 100 NEAR.
```

The test `meta_tx_fn_call_access_key_insufficient_allowance` in `integration-tests/src/tests/features/delegate_action.rs` confirms the relayer pays costs and the sender does not — the same accounting that makes this exploit possible. [7](#0-6)

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

**File:** runtime/runtime/src/lib.rs (L1281-1285)
```rust
        if deposit_refund > Balance::ZERO {
            result.new_receipts.push(Receipt::new_balance_refund(
                receipt.balance_refund_receiver(),
                deposit_refund,
            ));
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

**File:** docs/architecture/how/meta-tx.md (L237-242)
```markdown
and agrees to pay for it, so nothing wrong so far. But what if the transaction
fails execution on Bob's shard? At this point, the predecessor is `Alice` and
therefore she receives the token balance refunded, not the relayer. This is
something relayer implementations must be aware of since there is a financial
incentive for Alice to submit meta transactions that have high balances attached
but will fail on Bob's shard.
```

**File:** integration-tests/src/tests/features/delegate_action.rs (L392-428)
```rust
/// Call a function in a meta tx where the user only has access through a
/// function call access that has too little allowance left.
#[test]
fn meta_tx_fn_call_access_key_insufficient_allowance() {
    let sender = bob_account();
    let relayer = alice_account();
    let receiver = carol_account();

    // 1 yocto near, that's less than 1 gas unit
    let initial_allowance = Balance::from_yoctonear(1);
    let signer = create_user_test_signer(&sender);

    let node = setup_with_access_key(
        &relayer,
        &receiver,
        &sender,
        signer.public_key(),
        initial_allowance,
        TEST_METHOD,
    );

    let actions = vec![log_something_fn_call()];
    // this should still succeed because we use the gas of the relayer, not of the access key
    let outcome = check_meta_tx_fn_call(
        &node,
        actions,
        TEST_METHOD_LEN,
        Balance::ZERO,
        sender,
        relayer,
        receiver,
    );

    // Check that the function call was executed as expected
    let fn_call_logs = &outcome.receipts_outcome[1].outcome.logs;
    assert_eq!(fn_call_logs, &vec!["hello".to_owned()]);
}
```
