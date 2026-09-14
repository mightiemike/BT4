## Analog Vulnerability Found

### Title
Unvalidated `refund_to` beneficiary in `promise_set_refund_to` permanently burns attached deposits when the redirect target does not exist - ([File: runtime/near-vm-runner/src/wasmtime_runner/logic.rs])

### Summary
NEP-provided host function `promise_set_refund_to` lets a contract redirect the balance refund of an outgoing receipt to an arbitrary `AccountId` supplied as raw bytes. Like the Arrakis `unwraprefundeth`/`refund` address, this value is only syntactically validated (well-formed account-id string) — never checked for on-chain existence. If the redirected receipt fails and the chosen beneficiary account doesn't exist, the resulting refund is a "refund receipt" which — by protocol rule — can never implicitly create an account, so the transfer fails and the deposit is **permanently burned** instead of returned to anyone.

### Finding Description
`promise_set_refund_to` reads and parses the `refund_to` account id purely as a syntax check via `read_and_parse_account_id`, then stores it on the pending receipt with no existence check: [1](#0-0) 

The value is persisted via `ReceiptManager::set_refund_to`, again without any existence validation: [2](#0-1) 

`Receipt::balance_refund_receiver()` prefers `refund_to` over `predecessor_id` when generating the deposit refund: [3](#0-2) 

`refund_unspent_gas_and_deposits` builds the balance-refund receipt to that redirect target: [4](#0-3) 

Crucially, refund receipts (`predecessor_id == "system"`) are explicitly forbidden from creating any account, even in cases where a normal transfer would implicitly create one: [5](#0-4) 

So if the `refund_to` account does not exist, the transfer fails with `AccountDoesNotExist`, and per the runtime's failed-refund rule, the deposit is burned rather than returned: [6](#0-5) 

This exact scenario (deposit refund → nonexistent account → burn, not creation) is confirmed by an existing test: [7](#0-6) 

The protocol documentation itself states the destructive consequence plainly: "If the execution of a refund fails, the refund amount is burnt." [8](#0-7) 

The only validation ever performed on `refund_to` anywhere in the receipt lifecycle is the syntactic `AccountId::validate` check during receipt validation — it never checks account existence: [9](#0-8) 

### Impact Explanation
Any contract that calls `promise_set_refund_to` (directly reachable from a `FunctionCall` action originating from any ordinary transaction signer) with a beneficiary account id that is syntactically valid but does not exist on chain will have the deposit permanently destroyed if the redirected receipt subsequently fails. There is no mechanism to recover these funds — the tokens are burned from total supply, exactly mirroring the "funds lost due to unvalidated refund address" class from the external report. This qualifies as a concrete, protocol-level permanent loss of user funds triggered entirely by a normal transaction/contract-call path, with no attacker privilege required beyond calling a public host function from within a deployed contract (e.g. relaying/redirect logic, similar to the `spread()` example in the sharded test contract that itself uses `promise_set_refund_to`). [10](#0-9) 

### Likelihood Explanation
This is trivially reachable: any contract author (or a malicious/careless one interacting on a user's behalf) can set `refund_to` to a typo'd, never-created, or attacker-influenced account id (e.g., derived from user input without verifying the account was ever created). Since accounts are created independently of this call and there's no verification step, this can occur accidentally in normal contract development or be intentionally triggered to burn a victim's deposit if the beneficiary id is attacker-influenced input.

### Recommendation
Before accepting a `refund_to` redirection (or at minimum before finalizing the balance-refund receipt), verify the target account exists, or alternatively relax `implicit_creation_allowed` for the redirect case so that a refund routed via `refund_to` may implicitly create the destination account (consistent with how ordinary transfers create implicit accounts), preventing an unvalidated address from causing an unrecoverable burn.

### Proof of Concept
1. Deploy a contract that issues a cross-contract `FunctionCall` promise with an attached deposit to a receiver that will fail execution (e.g., call a non-existent method, as in `test_refund_to`).
2. Before dispatching, call `promise_set_refund_to(promise_idx, beneficiary_id)` where `beneficiary_id` is a syntactically valid AccountId that has never been created (e.g., a random 64-char hex/near-implicit-looking id never funded).
3. Let the receipt fail execution as intended.
4. Observe: the balance-refund receipt targets `beneficiary_id`; since it is a system-refund receipt, `implicit_creation_allowed` returns `false`, the transfer fails with `AccountDoesNotExist`, and the deposit amount is added to `other_burnt_amount` — permanently destroyed instead of returned to the predecessor or beneficiary. [11](#0-10)

### Citations

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L2559-2593)
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
}
```

**File:** runtime/runtime/src/receipt_manager.rs (L723-728)
```rust
    pub(super) fn set_refund_to(&mut self, receipt_index: ReceiptIndex, refund_to: AccountId) {
        self.action_receipts
            .get_mut(receipt_index as usize)
            .expect("receipt index should be valid for setting refund_to")
            .refund_to = Some(refund_to)
    }
```

**File:** core/primitives/src/receipt.rs (L428-430)
```rust
    pub fn balance_refund_receiver(&self) -> &AccountId {
        self.refund_to().as_ref().unwrap_or_else(|| self.predecessor_id())
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

**File:** runtime/runtime/src/actions.rs (L928-933)
```rust
/// Whether a transfer to an account that does not exist yet may create it.
fn implicit_creation_allowed(account_type: AccountType, receipt_shape: ReceiptShape) -> bool {
    let ReceiptShape { is_refund, is_the_only_action } = receipt_shape;
    if is_refund {
        return false; // Refund can never create an account
    }
```

**File:** runtime/runtime/src/tests/apply.rs (L6883-6926)
```rust
    fn refund_may_not_create_universal_account() {
        init_test_logger();
        let key = SecretKey::from_seed(KeyType::ED25519, "refund-target").public_key();
        let account_id = derive_universal_account_id(&state_init_for(&[key]).to_raw());
        let (runtime, tries, root, apply_state, _signers, epoch) = setup_runtime(
            vec![alice_account()],
            Balance::from_near(100),
            Balance::ZERO,
            Gas::from_teragas(1000),
        );

        let result = runtime
            .apply(
                tries.get_trie_for_shard(ShardUId::single_shard(), root),
                &None,
                &apply_state,
                from_ref(&Receipt::new_balance_refund(&account_id, funding())),
                SignedValidPeriodTransactions::empty(),
                &epoch,
                Default::default(),
            )
            .unwrap();
        let mut store_update = tries.store_update();
        let new_root =
            tries.apply_all(&result.trie_changes, ShardUId::single_shard(), &mut store_update);
        store_update.commit();

        // Assert on the reason, not just the absence: without this the test would
        // also pass if the refund receipt were dropped instead of refused.
        let [outcome] = &result.outcomes[..] else {
            panic!("the refund receipt must produce exactly one outcome, got {:?}", result.outcomes)
        };
        assert_matches!(
            &outcome.outcome.status,
            ExecutionStatus::Failure(TxExecutionError::ActionError(err))
                if matches!(err.kind, ActionErrorKind::AccountDoesNotExist { .. }),
            "a refund to a missing `0u` id must fail with AccountDoesNotExist",
        );
        let state = tries.new_trie_update(ShardUId::single_shard(), new_root);
        assert!(
            get_account(&state, &account_id).unwrap().is_none(),
            "a refund must not bring a `0u` account into existence",
        );
    }
```

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```

**File:** runtime/runtime/src/verifier.rs (L756-760)
```rust
    if let Some(account_id) = receipt.refund_to() {
        AccountId::validate(account_id.as_ref()).map_err(|_| {
            ReceiptValidationError::InvalidRefundTo { account_id: account_id.to_string() }
        })?;
    }
```

**File:** runtime/near-test-contracts/sharded-contract/src/lib.rs (L163-167)
```rust
    let send_balance = if attached > 0 {
        // Pass on refund_to, ensuring the predecessor gets the balance back in case of refund.
        refund_to_account_id(REG_A);
        promise_set_refund_to(promise_idx, u64::MAX, REG_A);
        attached
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
