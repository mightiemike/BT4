### Title
DeleteAccount refund to a nonexistent beneficiary permanently burns the deleted account's balance - (File: runtime/runtime/src/actions.rs)

### Summary
`action_delete_account` pays out the deleted account's remaining balance to `delete_account.beneficiary_id` by creating a refund receipt, without validating that the beneficiary account exists. If the beneficiary does not exist, the refund receipt fails on delivery and the value is permanently destroyed rather than returned to the account owner or any recoverable party — analogous to `transferFrom` moving an asset to an address that cannot receive it, resulting in permanent loss.

### Finding Description
When a `DeleteAccount` action executes, the deleted account's balance is not transferred synchronously; instead a `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)` is queued as a new outgoing receipt: [1](#0-0) 

There is no check anywhere in `action_delete_account` (or in `check_account_existence`/`check_actor_permissions`, which are the only permission/existence gates run per-action) that `beneficiary_id` refers to an existing, valid account. The account owner (or attacker in control of the delete transaction, e.g. via a full-access key) fully controls `beneficiary_id` and can set it to any syntactically valid `AccountId`, including one that has never been created.

When this refund receipt is later processed on the beneficiary's shard, it goes through the normal `Transfer` action-existence check. Because the receipt is a refund (`is_refund: true`), `implicit_creation_allowed` always returns `false` regardless of account type, so a missing beneficiary causes `check_account_existence` to return `ActionErrorKind::AccountDoesNotExist`: [2](#0-1) [3](#0-2) 

Per the documented runtime-execution flow, refund receipts are special-cased: "system-predecessor receipts (refund receipts) are free — no refund generated, and a failed refund burns its deposit into `other_burnt_amount`": [4](#0-3) 

This is confirmed by the existing test that exercises exactly this failure path (there for a `NearDeterministic`/universal-account address, but the mechanism is general to any missing named account beneficiary): [5](#0-4) 

So the sequence is: delete account with a mistyped/nonexistent `beneficiary_id` → account is removed and its balance is queued as a refund → refund arrives, `AccountDoesNotExist` fails the receipt → because it is a refund, no further refund is generated and the deposit is burned into `other_burnt_amount` instead of being returned to the original owner or anyone else. The value is irrecoverably lost from circulation, with no ownership path to reclaim it — the analog of an NFT vanishing because `transferFrom` sent it to an address incapable of holding it.

### Impact Explanation
This is a direct, protocol-level "permanently frozen/lost funds" bug class: any signer who deletes their own account (or any actor who obtains permission to call `DeleteAccount`, e.g. a compromised or careless full-access key, or a contract that issues `promise_batch_action_delete_account` with an attacker/user-controlled beneficiary) can cause the entire remaining NEAR balance of the account to be permanently burned rather than delivered, simply by specifying a beneficiary account id that does not exist (e.g. a typo, or an address nobody has claimed yet). There is no recovery mechanism — the funds are converted to `tokens_burnt`/`other_burnt_amount`, not credited to any account. This matches the "permanently frozen funds" acceptance criterion.

### Likelihood Explanation
High reachability: `DeleteAccount` is an ordinary, unprivileged action available both directly in a signed transaction and via `promise_batch_action_delete_account` from any contract call: [6](#0-5) 
The `beneficiary_id` is fully attacker/user-controlled and is never validated for existence before the account is deleted and the payout receipt is queued. A simple typo, an account that expired/was never created, or a maliciously crafted contract that deletes a victim account with a bogus beneficiary (where permitted by `actor_id` checks) triggers the loss deterministically and unconditionally — no race condition or special network state required.

### Recommendation
Before deleting the account and queuing the balance-refund receipt, verify that `delete_account.beneficiary_id` corresponds to an existing account (or otherwise falls back to a guaranteed-valid recipient, such as returning `ActionErrorKind::AccountDoesNotExist` and aborting the deletion, or refunding the balance back to the account being deleted / the signer rather than an unchecked beneficiary). At minimum, since refunds are known to be able to fail and burn silently, `action_delete_account` should reject the action up front (mirroring how `CreateAccount`/`Transfer` validate account existence) instead of only discovering the failure asynchronously on a different shard after the account has already been irreversibly removed.

### Proof of Concept
1. Account `alice.near` holds a positive balance and calls `DeleteAccount { beneficiary_id: "nonexistent123.near" }` (an account id that was never created).
2. `action_delete_account` removes `alice.near`'s state and pushes `Receipt::new_balance_refund("nonexistent123.near", account_balance)` as shown in `runtime/runtime/src/actions.rs:380-387`.
3. On the shard hosting `nonexistent123.near`, the refund receipt's `Transfer` action is checked by `check_account_existence`; since `is_refund == true`, `implicit_creation_allowed` returns `false` unconditionally, so the action fails with `AccountDoesNotExist` (`runtime/runtime/src/actions.rs:928-947`, `842-850`).
4. Because the failed receipt is itself a refund, no secondary refund is issued; the deposit is instead burned into `other_burnt_amount` per the documented behavior (`protocol-model/spec/runtime-execution.md:69`), matching the pattern already unit-tested in `refund_may_not_create_universal_account` (`runtime/runtime/src/tests/apply.rs:6880-6926`).
5. Result: `alice.near`'s entire balance is permanently destroyed with no recipient, recoverable only by protocol-level intervention.

### Citations

**File:** runtime/runtime/src/actions.rs (L380-387)
```rust
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
    let remove_result = remove_account(state_update, account_id)?;
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

**File:** protocol-model/spec/runtime-execution.md (L69-69)
```markdown
6. **Refunds** (see below): system-predecessor receipts (refund receipts) are free — no refund generated, and a failed refund burns its deposit into `other_burnt_amount` (`runtime/runtime/src/lib.rs:929`). Otherwise `refund_unspent_gas_and_deposits` runs (`:943`).
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

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L4038-4072)
```rust
pub fn promise_batch_action_delete_account(
    ctx: &mut Ctx,
    memory: &mut [u8],
    promise_idx: u64,
    beneficiary_id_len: u64,
    beneficiary_id_ptr: u64,
) -> Result<()> {
    ctx.result_state.gas_counter.pay_base(base)?;
    if ctx.context.is_view() {
        return Err(HostError::ProhibitedInView {
            method_name: "promise_batch_action_delete_account".to_string(),
        }
        .into());
    }
    let beneficiary_id = read_and_parse_account_id(
        &mut ctx.result_state.gas_counter,
        memory,
        &ctx.registers,
        &ctx.config,
        beneficiary_id_ptr,
        beneficiary_id_len,
    )?;

    let (receipt_idx, sir) = promise_idx_to_receipt_idx_with_sir(ctx, promise_idx)?;

    pay_action_base(
        &mut ctx.result_state.gas_counter,
        &ctx.fees_config,
        ActionCosts::delete_account,
        sir,
    )?;

    ctx.ext.append_action_delete_account(receipt_idx, beneficiary_id);
    Ok(())
}
```
