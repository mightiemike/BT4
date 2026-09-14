### Title
Unvalidated `promise_set_refund_to` destination causes attached deposits to be permanently burnt instead of refunded - (File: `runtime/runtime/src/lib.rs`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

### Summary
The GatewaySend bug used an ERC20-only transfer primitive for native ETH refunds, so the refund call reverted and the funds became permanently stuck. NEAR has an analogous "wrong-path refund" hazard: a contract can redirect an outgoing receipt's balance refund to an arbitrary account via `promise_set_refund_to`/`refund_to_account_id`, and the protocol never validates that this redirect target exists or is reachable. If the downstream receipt fails and the refund is then routed to that account and the refund transfer itself fails (e.g., target does not exist), the runtime does not restore the funds to the depositor — it permanently burns the deposit as `other_burnt_amount`.

### Finding Description
Refund receipts are generated as a plain `Transfer` action to a `receiver_id` chosen either as `predecessor_id` or, if the predecessor used `promise_set_refund_to`, as the account the predecessor supplied: [1](#0-0) 

This host function accepts a raw account-id string with no validation that it currently exists on-chain, and is documented as letting "the predecessor... set this to another account id when sending the receipt": [2](#0-1) 

When execution of that redirected refund receipt itself fails (`predecessor_id.is_system()` and `result.result.is_err()`), the protocol does not retry or fall back to the original depositor — it burns the deposit permanently: [3](#0-2) 

This is documented, intended behavior for refund failures generally: "If the execution of a refund fails, the refund amount is burnt": [4](#0-3) 

This is exercised and confirmed by an existing test showing a refund to a non-existent (`0u`) account fails with `AccountDoesNotExist` and the account is never created — i.e. the deposit is lost rather than returned: [5](#0-4) 

The redirection feature itself is exercised in `test_refund_to`, showing the redirected refund is routed to whatever account the predecessor named, with no existence check performed at the time `promise_set_refund_to` is called: [6](#0-5) 

Any contract that forwards a caller-supplied "refund address" through `promise_set_refund_to` (a legitimate and expected usage pattern for relayer/bridge-style contracts, exactly analogous to `GatewaySend`) inherits this failure mode: a typo'd, deleted, or otherwise-unreachable refund target silently converts a temporary failed-call deposit into a permanently destroyed one.

### Impact Explanation
This matches the "permanently frozen funds" (here, permanently destroyed funds — an even stronger outcome) category. Deposits attached by a legitimate depositor to a cross-contract call can be irrecoverably burnt due to a downstream contract's choice of refund target, with no protocol-level backstop routing the value back to the original signer/predecessor. Since `promise_set_refund_to` is a documented, unprivileged, contract-callable host function (reachable by any account via a `FunctionCall` action), this is directly reachable by any transaction signer or contract deployer, matching the required threat model.

### Likelihood Explanation
Likelihood is contract-usage-dependent rather than a pure protocol bug: it requires a contract (bridge/relayer/router-style contract, exactly the `GatewaySend`-analog use case) to pass through or mis-set a refund-to account id without validating its existence, combined with the downstream call failing. Given `promise_set_refund_to` was specifically added to let contracts redirect refunds to third parties (NEP-related feature, changelog `#14285`), and cross-chain/bridge contracts on NEAR commonly need exactly this pattern to return funds to depositors on failure, the pattern is realistically reachable in production dApps, mirroring the exact scenario in the referenced Sherlock report.

### Recommendation
Consider one or more of:
- Validating (at refund-redirect time or at refund-execution time) that the `refund_to` account exists, and if not, falling back to `predecessor_id` instead of burning.
- On refund-receipt failure, instead of unconditionally burning, retry with the original predecessor as a fallback destination.
- Emitting a stronger warning/require in `promise_set_refund_to`'s documentation and SDKs cautioning integrators (bridge/relayer contracts) that an invalid or malicious refund target results in permanent loss of the caller's deposit, since the protocol provides no safety net.

### Proof of Concept
1. Contract `A` receives a deposit from user `U` intended to be forwarded through a cross-contract call to `B` (analogous to `GatewaySend.depositAndCall`).
2. `A` creates a promise to `B` with `promise_batch_action_function_call` and an attached deposit, then calls `promise_set_refund_to(promise_idx, refund_account)`, where `refund_account` is a user-supplied or programmatically derived string that does not correspond to an existing account (e.g., mistyped bridging identifier, or an account that self-deleted between request and execution).
3. The call to `B` fails (e.g., `B` rejects the deposit for a valid reason, analogous to the destination-chain revert in the DODO case).
4. The runtime creates a `Receipt::new_balance_refund`-style transfer receipt to `refund_account`.
5. That transfer receipt fails execution with `ActionErrorKind::AccountDoesNotExist` because the target account doesn't exist (as verified in `refund_may_not_create_universal_account`, `runtime/runtime/src/tests/apply.rs:6883-6926`).
6. Per `runtime/runtime/src/lib.rs:1047-1054`, since `predecessor_id.is_system()` and the refund failed, the deposit is added to `other_burnt_amount` — permanently destroyed, never returned to `U` or `A`.

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

**File:** docs/RuntimeSpec/Components/BindingsSpec/ContextAPI.md (L103-119)
```markdown
#### refund_to_account_id

```rust
refund_to_account_id(register_id: u64)
```

If a receipt fails execution, a balance refund usually goes to the predecessor of the receipt. However, the predecessor
can set this to another account id when sending the receipt.

###### Normal operation

- Saves the bytes of the account id receiving balance refunds into the register.

###### Panics

- If the registers exceed the memory limit panics with `MemoryAccessViolation`;
- If called in a view function panics with `ProhibitedInView`.
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

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```

**File:** runtime/runtime/src/tests/apply.rs (L6880-6926)
```rust
    /// The other half of the old gate, untouched by the relaxation: refunds are
    /// free, so they must not create an account, a `0u` one included.
    #[test]
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
