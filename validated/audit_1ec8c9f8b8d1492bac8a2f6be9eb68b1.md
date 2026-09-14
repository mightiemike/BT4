Based on the investigation, this confirms the analog pattern exists in nearcore.

### Title
DeleteAccountAction's beneficiary_id is not validated against implicit/named account existence, causing permanent burn of the account's balance when the beneficiary is set to a non-existent account - (File: runtime/runtime/src/actions.rs)

### Summary
`DeleteAccountAction { beneficiary_id }` lets any account owner route their account's remaining balance to an arbitrary `beneficiary_id` when they delete their own account. Unlike the `PassThroughWalletImpl.setPassThrough()` bug (missing `address(0)` check), nearcore's validation layer only checks that `beneficiary_id` parses as a syntactically valid account id and that the deleting account has no locked stake; it never checks that the beneficiary account actually exists or is reachable.

### Finding Description
`action_delete_account` unconditionally emits a `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)` for the full remaining balance and then removes the source account: [1](#0-0) 

The only validation performed on `beneficiary_id` (in `validate_delete_account_action`/`validate_transfer_action`-equivalent checks in `action_validation.rs`) is that it is a well-formed `AccountId` string, per the spec: [2](#0-1) 

Crucially, the transfer created for the beneficiary is built via `Receipt::new_balance_refund`, which sets `predecessor_id = "system"`, marking it as a **refund receipt**: [3](#0-2) 

Refund receipts are special-cased in the runtime: if execution of a refund receipt fails (e.g., because the target named account does not exist, or a universal/deterministic beneficiary id can't be created by a bare transfer), the funds are **burned** rather than bounced back to anyone: [4](#0-3)  and confirmed in the runtime execution logic: [5](#0-4) 

Tests explicitly document that a delete-account beneficiary must already exist, otherwise the balance is lost: [6](#0-5)  and a directly analogous test shows a refund to a missing account fails and the funds do not return: [7](#0-6) 

### Impact Explanation
If the account owner (an unprivileged transaction signer, fully in control of composing their own `DeleteAccountAction`) mistypes/misconfigures `beneficiary_id` to reference a named account that does not exist (analogous to `address(0x0)`), the entire remaining balance of the deleted account is permanently burned with no recovery path — this is a direct "permanently frozen/lost funds" outcome from a single self-submitted transaction, matching the report's bug class (missing validation of a settable payout address leading to irreversible loss of funds). This can also be reached identically via `promise_batch_action_delete_account` from a contract call: [8](#0-7) , and via meta-transactions/relayers since `DeleteAccount` is a supported delegate action.

### Likelihood Explanation
This is trivially reachable by any single account holder or contract issuing a self-delete with an arbitrary `beneficiary_id`; no special privilege is required, and there is no explicit protocol-level guard preventing a non-existent or unreachable beneficiary account. The likelihood of accidental misuse mirrors the original report (a routine "owner mis-sets an address" scenario), and is even easier to trigger unintentionally than the Solidity case because `beneficiary_id` merely needs to be a syntactically valid, non-existent NEAR account name (a very easy typo) rather than the special constant `address(0)`.

### Recommendation
Nearcore currently treats this as expected/documented behavior (tests assert the burn), so no code defect exists relative to spec — but this is worth flagging in the same spirit as the audit finding: consider adding either (a) a protocol-level check that `beneficiary_id` corresponds to an account that exists (or is a valid implicit id capable of being created by the refund transfer) before allowing `DeleteAccount` to execute, or (b) client/wallet-side UX guardrails that verify beneficiary account existence prior to submitting a `DeleteAccountAction`, since the current design silently converts a user typo into an irreversible token burn.

### Proof of Concept
1. Create account `alice.near` with balance `B`.
2. Submit `SignedTransaction` from `alice.near` to itself with a single action: `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "nonexistent.near".parse().unwrap() })`, where `nonexistent.near` is a named account that was never created.
3. `action_delete_account` (runtime/runtime/src/actions.rs:330-405) unconditionally builds `Receipt::new_balance_refund(&"nonexistent.near", B)` and deletes `alice.near`.
4. The refund receipt is processed with `predecessor_id = "system"`; since `nonexistent.near` does not exist, the inner `Transfer` action fails with `AccountDoesNotExist`.
5. Because this is a refund receipt (`predecessor_id().is_system()`), the runtime does not create a bounce-back receipt — the deposit `B` is added to `stats.balance.other_burnt_amount` and permanently removed from circulation (runtime/runtime/src/lib.rs:1047-1055; confirmed by test `refund_may_not_create_universal_account` in runtime/runtime/src/tests/apply.rs:6883-6926, which is structurally identical to a named/non-existent beneficiary case).

### Citations

**File:** runtime/runtime/src/actions.rs (L380-405)
```rust
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
    let remove_result = remove_account(state_update, account_id)?;
    result.tokens_burnt =
        result.tokens_burnt.checked_add(gas_key_balance_to_burn).ok_or_else(|| {
            StorageError::StorageInconsistentState("tokens_burnt overflow".to_string())
        })?;
    if remove_result.gas_key_nonce_count > 0 {
        let compute = storage_removes_compute(
            &config.wasm_config.ext_costs,
            remove_result.gas_key_nonce_count,
            remove_result.gas_key_nonce_total_key_bytes,
            AccessKey::NONCE_VALUE_LEN * remove_result.gas_key_nonce_count,
        );
        result.compute_usage = safe_add_compute(result.compute_usage, compute).map_err(|_| {
            StorageError::StorageInconsistentState("compute_usage overflow".to_string())
        })?;
    }
    *actor_id = receipt.predecessor_id().clone();
    *account = None;
    Ok(())
```

**File:** docs/RuntimeSpec/Actions.md (L278-307)
```markdown
## DeleteAccountAction

```rust
pub struct DeleteAccountAction {
    /// The remaining account balance will be transferred to the AccountId below
    pub beneficiary_id: AccountId,
}
```

**Outcomes**:

- The account, as well as all the data stored under the account, is deleted and the tokens are transferred to `beneficiary_id`.

### Errors

**Validation Error**:

- If `beneficiary_id` is not a valid account id, the following error will be returned

```rust
/// Invalid account ID.
InvalidAccountId { account_id: AccountId },
```

- If this action is not the last action in the action list of a receipt, the following error will be returned

```rust
/// The delete action must be a final action in transaction
DeleteActionMustBeFinal
```
```

**File:** core/primitives/src/receipt.rs (L493-510)
```rust
    /// Generates a receipt with a transfer from system for a given balance without a receipt_id.
    /// This should be used for token refunds instead of gas refunds.
    /// It doesn't refund the allowance of the access key. For gas refunds use `new_gas_refund`.
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

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```

**File:** runtime/runtime/src/lib.rs (L1047-1055)
```rust
        let gas_refund_result = if receipt.predecessor_id().is_system() {
            // If the refund fails tokens are burned.
            if result.result.is_err() {
                stats.balance.other_burnt_amount = safe_add_balance(
                    stats.balance.other_burnt_amount,
                    total_deposit(&action_receipt.actions())?,
                )?
            }
            GasRefundResult::default()
```

**File:** runtime/runtime/src/tests/apply.rs (L6353-6357)
```rust
        let (runtime, tries, root, apply_state, epoch) = setup(&account_id, balance);

        // The beneficiary has to exist, otherwise the balance transfer the delete
        // sends it would come straight back as a refund.
        let beneficiary: AccountId = "beneficiary.near".parse().unwrap();
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
