I have sufficient evidence to confirm the analog vulnerability. Let me finalize the answer.

### Title
Redirecting balance refunds via `promise_set_refund_to` to a nonexistent account permanently burns the refunded deposit - (File: runtime/runtime/src/receipt_manager.rs, runtime/runtime/src/actions.rs, runtime/runtime/src/lib.rs)

### Summary
NEAR's `promise_set_refund_to` host function lets a contract redirect the balance refund of an outgoing receipt to any account it names [1](#0-0) . If that receipt's execution later fails and the resulting refund receipt targets an account that does not exist, the refund transfer is rejected with `AccountDoesNotExist`, and the runtime burns the entire refunded deposit instead of returning it to anyone. This mirrors the reported Arbitrum `L1GraphTokenGateway` issue, where a caller-controlled refund address that cannot receive funds on the destination chain causes the transferred value to be permanently lost.

### Finding Description
`Receipt::balance_refund_receiver()` returns the contract-supplied `refund_to` account if one was set, otherwise the predecessor [2](#0-1) . When a receipt fails, `refund_unspent_gas_and_deposits` builds a system-predecessor deposit-refund receipt addressed to that account via `Receipt::new_balance_refund` [3](#0-2) .

When this refund receipt is later applied, `apply_action_receipt` treats it as a refund (`predecessor_id == "system"`), and `check_account_existence`/`implicit_creation_allowed` explicitly forbid a refund from creating any account, regardless of type [4](#0-3) . So if the `refund_to` account does not exist (or is later deleted before the refund arrives, since it may take multiple blocks/shards to be delivered), the `Transfer` action fails with `AccountDoesNotExist` [5](#0-4) .

Because this is a refund receipt, the runtime does not retry or reroute the funds anywhere — it burns them: `apply_action_receipt` checks `receipt.predecessor_id().is_system()` and, on failure, adds the full deposit to `other_burnt_amount` [6](#0-5) . This exact behavior is documented: "If the execution of a refund fails, the refund amount is burnt" [7](#0-6) .

`validate_action_receipt` only checks that `refund_to` is a syntactically valid `AccountId`, not that the account exists , so nothing in the transaction/receipt validation path prevents a contract from setting a `refund_to` that will predictably or unpredictably fail to exist when the refund is delivered.

### Impact Explanation
Any smart contract that uses `promise_set_refund_to` to redirect a refund to a named account that does not yet exist (typo, not-yet-created account, race with a `DeleteAccount` on the beneficiary that happens before the refund is delivered, since cross-shard delivery can span multiple blocks) causes the deposit attached to that receipt to be irrecoverably burned rather than refunded to anyone. This is a concrete, protocol-level, transaction-triggered value loss: funds that should be returned to a party are destroyed. It matches the judged severity (Medium) of the original finding — a leak/loss of value reachable via a normal call path rather than a direct theft.

### Likelihood Explanation
This is trivially reachable by any contract deployer or any dApp integrating `promise_set_refund_to` (e.g., fee-splitting, referral, or escrow-style patterns that redirect refunds to a third-party beneficiary account). No privileged role is required — a single contract call from any account can set an incorrect or as-yet-uncreated `refund_to`, and normal receipt failure (e.g., insufficient gas on the downstream call, `MethodNotFound`, or any other action failure) triggers the burn path. The additional race-condition variant (beneficiary deleted between refund creation and refund delivery) requires no attacker cooperation at all — it can happen through the ordinary, asynchronous nature of cross-shard receipt delivery.

### Recommendation
- Validate (at least at the point `promise_set_refund_to` is called, or when the balance-refund receipt is generated) whether the account is a plausible target, and disallow it or fall back to the original predecessor if the `refund_to` account cannot be confirmed to exist.
- Consider not burning the deposit outright on a failed refund to a `refund_to` account; instead fall back to refunding the original receipt's `predecessor_id` (which is guaranteed to exist, since it authored the receipt) when the custom `refund_to` account does not exist.
- Document explicitly (in the `promise_set_refund_to` / `refund_to_account_id` host-function docs) that setting a nonexistent or later-deleted account as `refund_to` results in permanent loss of the refunded deposit, so integrators are aware of the risk, mirroring the acknowledgment requested in the original Arbitrum finding.

### Proof of Concept
1. Contract `A` (called by any user transaction) creates a promise/receipt targeting contract `B` with a nonzero `deposit`, e.g. a `FunctionCall` action with `amount = deposit`.
2. `A` calls `promise_set_refund_to(promise_index, "not-yet-created.near")`, redirecting the deposit refund to a named account that does not exist on chain [8](#0-7) .
3. The receipt to `B` fails (e.g., calls a nonexistent method), so `refund_unspent_gas_and_deposits` creates a balance-refund receipt with `receiver_id = "not-yet-created.near"` and `predecessor_id = "system"` [9](#0-8) .
4. When this refund receipt is applied, `check_account_existence` rejects the `Transfer` action because `implicit_creation_allowed` returns `false` for a refund receipt [10](#0-9) , producing `ActionErrorKind::AccountDoesNotExist`.
5. Since the refund's predecessor is `system`, the runtime burns the deposit (`other_burnt_amount`) instead of returning it to `A`, `B`, or anyone else [6](#0-5) .

This flow is exercised (for a successful case) in the existing `test_refund_to` test, which shows the redirected refund mechanism in action [11](#0-10) ; substituting a nonexistent `beneficiary_id` demonstrates the burn path instead of a successful refund.

### Citations

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L2559-2592)
```rust
pub fn promise_set_refund_to(
    ctx: &mut Ctx,
    memory: &mut [u8],
    promise_idx: u64,
    account_id_len: u64,
    account_id_ptr: u64,
) -> Result<()> {
    ctx.result_state.gas_counter.pay_base(base)?;
    if ctx.context.is_view() {
        return Err(HostError::ProhibitedInView {
            method_name: "promise_set_refund_to".to_string(),
        }
        .into());
    }
    let refund_to = read_and_parse_account_id(
        &mut ctx.result_state.gas_counter,
        memory,
        &ctx.registers,
        &ctx.config,
        account_id_ptr,
        account_id_len,
    )?;
    let promise = ctx
        .promises
        .get(promise_idx as usize)
        .ok_or(HostError::InvalidPromiseIndex { promise_idx })?;

    let receipt_idx = match &promise {
        Promise::Receipt(receipt_idx) => Ok(*receipt_idx),
        Promise::NotReceipt(_) => Err(HostError::CannotSetRefundToOnJointPromise),
    }?;

    ctx.ext.set_refund_to(receipt_idx, refund_to);
    Ok(())
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

**File:** runtime/runtime/src/lib.rs (L1047-1054)
```rust
        let gas_refund_result = if receipt.predecessor_id().is_system() {
            // If the refund fails tokens are burned.
            if result.result.is_err() {
                stats.balance.other_burnt_amount = safe_add_balance(
                    stats.balance.other_burnt_amount,
                    total_deposit(&action_receipt.actions())?,
                )?
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

**File:** runtime/runtime/src/actions.rs (L842-850)
```rust
        Action::Transfer(_) => {
            let account_type = get_account_type(account_id, config);
            if account.is_none() && !implicit_creation_allowed(account_type, receipt_shape) {
                return Err(ActionErrorKind::AccountDoesNotExist {
                    account_id: account_id.clone(),
                }
                .into());
            }
        }
```

**File:** runtime/runtime/src/actions.rs (L928-947)
```rust
/// Whether a transfer to an account that does not exist yet may create it.
fn implicit_creation_allowed(account_type: AccountType, receipt_shape: ReceiptShape) -> bool {
    let ReceiptShape { is_refund, is_the_only_action } = receipt_shape;
    if is_refund {
        return false; // Refund can never create an account
    }

    match account_type {
        // Named accounts can never be implicitly created by transfer
        AccountType::NamedAccount => false,
        // Near-implicit, Eth-implicit, and deterministic accounts can only be created
        // if transfer is the only action, to avoid account hijacking.
        AccountType::NearImplicitAccount
        | AccountType::EthImplicitAccount
        | AccountType::NearDeterministicAccount => is_the_only_action,
        // Universal account creation does NOT require transfer to be the only action.
        // It cannot be hijacked by other actions batched with the transfer.
        AccountType::UniversalAccount => true,
    }
}
```

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```

**File:** runtime/near-test-contracts/test-contract-rs/src/lib.rs (L1087-1095)
```rust
            } else if let Some(action) = arg.get("set_refund_to") {
                let promise_index = action["promise_index"].as_i64().unwrap() as u64;
                let beneficiary_id = action["beneficiary_id"].as_str().unwrap().as_bytes();
                promise_set_refund_to(
                    promise_index,
                    beneficiary_id.len() as u64,
                    beneficiary_id.as_ptr() as u64,
                );
                promise_index
```

**File:** runtime/runtime/tests/test_async_calls.rs (L1204-1296)
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
    ]);

    let signed_transaction = SignedTransaction::from_actions(
        1,
        signer_sender.get_account_id(),
        signer_receiver.get_account_id(),
        &signer_sender,
        vec![Action::FunctionCall(Box::new(FunctionCallAction {
            method_name: "call_promise".to_string(),
            args: serde_json::to_vec(&data).unwrap(),
            gas: GAS_1,
            deposit,
        }))],
        CryptoHash::default(),
    );

    let handles = RuntimeGroup::start_runtimes(group.clone(), vec![signed_transaction.clone()]);
    for h in handles {
        h.join().unwrap();
    }

    println!("{:?}", group.executed_receipts);

    use near_primitives::transaction::*;
    let [r0] = &*assert_receipts!(group, signed_transaction) else {
        panic!("Incorrect number of produced receipts")
    };

    let receipts = &*assert_receipts!(group, "near_0" => r0 @ "near_1",
        ReceiptEnum::Action(ActionReceipt{actions, ..}) | ReceiptEnum::ActionV2(ActionReceiptV2{actions, ..}),
        {},
        actions,
        a0, Action::FunctionCall(function_call_action), {
            assert_eq!(function_call_action.gas, GAS_1);
            assert_eq!(function_call_action.deposit, deposit);
            assert_eq!(function_call_action.method_name, "call_promise");
        }
    );
    let [r1, refunds @ ..] = &receipts else { panic!("Incorrect number of produced receipts") };
    group.assert_gas_refunds(&refunds[..]);

    let receipts = &*assert_receipts!(group, "near_1" => r1 @ "near_2",
        ReceiptEnum::Action(ActionReceipt{actions, ..}) | ReceiptEnum::ActionV2(ActionReceiptV2{actions, ..}),
        {},
        actions,
        a0, Action::FunctionCall(function_call_action), {
            assert_eq!(function_call_action.gas, GAS_2);
            assert_eq!(function_call_action.deposit, deposit);
            assert_eq!(function_call_action.method_name, "non_existing_function");
        }
    );
    // The redirected deposit refund (to `near_3`) is emitted first; any trailing receipt is the
    // gas refund for executing this receipt.
    let [deposit_refund, refunds @ ..] = &receipts else {
        panic!("Incorrect number of produced receipts")
    };
    group.assert_gas_refunds(&refunds[..]);

    // This is the redirected refund
    assert_refund!(group, deposit_refund @ "near_3");
}
```
