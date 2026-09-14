I have enough evidence to construct the finding now.

### Title
Deposit refunds routed via `refund_to` (or to a deleted account) are permanently burned instead of returned to the funder - ([File: runtime/runtime/src/lib.rs])

### Summary
When a receipt fails (or a batched promise redirects its balance refund via `promise_batch_action_set_refund_to`), the runtime generates a `Receipt::new_balance_refund` addressed to `receipt.balance_refund_receiver()`. If that receiver account does not exist (never created, deleted between send and execution, or an implicit/universal account whose id was never actually claimed), the refund receipt fails with `AccountDoesNotExist` and, per documented protocol behavior, the deposit is **irrecoverably burned** rather than returned to anyone. This is the direct NEAR analog of the OpenQ finding: a legitimate funder can end up permanently unable to reclaim their attached deposit, except here the outcome is strictly worse — total destruction of the value instead of it merely being stuck.

### Finding Description
Deposit refunds are created by `refund_unspent_gas_and_deposits` in [1](#0-0)  which pushes a `Receipt::new_balance_refund(receipt.balance_refund_receiver(), deposit_refund)`. The refund target is resolved by `Receipt::balance_refund_receiver`, which uses the receipt's `refund_to` field if set (via `promise_batch_action_set_refund_to`/`set_refund_to`), or otherwise falls back to `predecessor_id`: [2](#0-1) 

Refund receipts are recognizable because `predecessor_id == "system"` and are documented as free/no-gas execution, but with a critical caveat: [3](#0-2) 

Crucially, refund receipts can **never** implicitly create the destination account, even if that account id is of a type that would normally support implicit creation: [4](#0-3) 

This is enforced explicitly and tested: a balance-refund receipt aimed at a nonexistent account (e.g. a universal/implicit id, or any account deleted before the refund lands) fails with `AccountDoesNotExist`, and the account is confirmed to never be created as a side effect: [5](#0-4) 

When this failure happens, `apply_action_receipt` treats it as a refund-receipt failure and burns the deposit into `other_burnt_amount` instead of trying again or falling back to any other account: [6](#0-5) 

The `test_refund_to` test demonstrates the exact reachable path: an ordinary account signs a transaction that batches a cross-contract `FunctionCall` with an attached deposit and calls `set_refund_to` to redirect the eventual deposit refund to a third account of the caller's choosing: [7](#0-6) 

Because `refund_to`/`predecessor_id` account existence is only checked at refund-execution time (potentially in a different shard/block than when the original receipt was created), there is a real window in which the intended recipient of the refund (the funder, or any account they nominate) is deleted (e.g., via `DeleteAccount`) or was never actually a valid/existing account, causing the eventual balance-refund receipt to fail and the deposit to be burned rather than returned.

### Impact Explanation
This causes concrete, unauthorized value destruction reachable from a single ordinary transaction: a funder who attaches a NEAR deposit to a cross-contract call (or redirects the refund target via `set_refund_to`) can lose that deposit permanently if the destination account for the refund ceases to exist (or never existed) by the time the refund receipt executes — with no path to recovery, since refunds are explicitly barred from implicitly creating accounts. This matches the "funder cannot reclaim their deposit" class from the source report, but resolves in outright token burning rather than a merely stuck balance, which is a strictly more severe (Medium/High) instance of frozen/lost user funds.

### Likelihood Explanation
Likelihood is moderate: it requires either (a) an account deletion racing with an in-flight cross-shard refund receipt for the same account, or (b) a caller intentionally/accidentally using `promise_batch_action_set_refund_to` (or a contract library built on it, e.g. escrow/bounty-style contracts holding third-party deposits analogous to the original OpenQ contract) to target an account id that turns out not to exist by refund time. Both scenarios are reachable purely by unprivileged transaction signers/contract callers without any special privilege, and the deletion race is fully attacker- or user-triggerable via ordinary `DeleteAccount` actions.

### Recommendation
Consider hardening the refund path so that a failed balance-refund due to a missing destination does not unconditionally burn funds: e.g., fall back to a well-defined, always-existing account (protocol treasury) with clear accounting semantics, or disallow refund redirection (`refund_to`) to accounts that are not verified to persist, or provide an explicit, auditable mechanism/warning to callers that `set_refund_to` targets sharing this fate is a documented burn risk so higher-level contracts (like escrow/bounty patterns) avoid using end users' potentially-deletable accounts as refund destinations for pooled funds they don't ultimately control.

### Proof of Concept
1. Account `A` sends a transaction with an attached deposit `D` to `A`'s own or another contract, batching a cross-contract promise that calls `promise_batch_action_set_refund_to(promise_index, "B")`, per `runtime/near-vm-runner/src/wasmtime_runner/logic.rs` / `runtime/runtime/src/receipt_manager.rs`, redirecting this promise's deposit-refund destination to account `B` instead of the default predecessor.
2. Before the downstream receipt fails and its deposit-refund receipt (`Receipt::new_balance_refund`) is processed (which can be in a later block/shard), account `B` is deleted (via `DeleteAccount`) — or `B` is chosen to be an account id that was never created (e.g., a universal/implicit-style id never actually initialized).
3. The downstream action fails as intended (e.g., calling a non-existing method), triggering `refund_unspent_gas_and_deposits` to emit `Receipt::new_balance_refund(&"B", D)` as demonstrated in `runtime/runtime/tests/test_async_calls.rs::test_refund_to`.
4. When this refund receipt executes, `check_account_existence`/`implicit_creation_allowed` reject account creation for refunds (`runtime/runtime/src/actions.rs:928-933`), the action fails with `AccountDoesNotExist` (mirrored by the existing unit test `refund_may_not_create_universal_account` in `runtime/runtime/src/tests/apply.rs:6883-6926`), and per `apply_action_receipt`'s refund-failure branch (`runtime/runtime/src/lib.rs:1047-1054`), the deposit `D` is added to `other_burnt_amount` — permanently destroyed, never returned to `A` or `B`.

### Citations

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

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
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

**File:** runtime/runtime/tests/test_async_calls.rs (L1204-1236)
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
```
