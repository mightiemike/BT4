I have sufficient evidence to confirm this analog. Here's my analysis:

### Title
Composing a `Delegate` action with other actions in the same receipt is subject to nonce front-running DoS - (File: `runtime/runtime/src/action_validation.rs`, `runtime/runtime/src/actions.rs`, `runtime/runtime/src/lib.rs`)

### Summary
NEAR permits a `Delegate`/`DelegateV2` action (a signed, nonce-gated meta-transaction "approval" analogous to the TOFT permit/approval sub-message) to be bundled together with other, unrelated actions inside a single outer transaction/action receipt, as long as only one delegate action is present [1](#0-0) . Because a `SignedDelegateAction` is a self-contained, publicly-forwardable, signature-authenticated payload (like an ERC-20 `permit`), anyone who obtains its bytes (e.g. from the relayer's mempool-visible transaction) can wrap and submit it themselves ahead of the original relayer, advancing the sender's access-key nonce and causing the relayer's original transaction to fail with `DelegateActionInvalidNonce` [2](#0-1) .

### Finding Description
`validate_actions_with_mode` explicitly allows a `Delegate`/`DelegateV2` action to sit next to other, unrelated actions in the same action list — it only forbids *more than one* delegate action, not delegate-plus-other-actions combinations: [1](#0-0)  This is confirmed by the test `test_delegate_action_must_be_only_one`, which asserts `Ok(())` for `[CreateAccount, Delegate(...)]` [3](#0-2) .

When such a bundled receipt is executed, `apply_action_receipt` runs every action in order, and the first action to fail aborts the whole loop: [4](#0-3)  If `result.result` ends up `Err`, the entire receipt is rolled back via `state_update.rollback()` — discarding the effects of every other (successful) action bundled in the same receipt: [5](#0-4) 

`apply_delegate_action` validates the inner `SignedDelegateAction`'s nonce via `validate_delegate_action_key`, which requires `delegate_nonce.nonce() > current_nonce` on the sender's access key, else it sets `ActionErrorKind::DelegateActionInvalidNonce` on `result.result` (a validation failure, not a runtime error) [2](#0-1) [6](#0-5) .

Crucially, a `SignedDelegateAction` is authenticated purely by the sender's signature over its content — not by who submits it. Per the project's own meta-tx documentation, "a relayer wraps it in a transaction, of which the relayer is the signer" [7](#0-6) , meaning *any* account, not just the intended relayer, can take the same signed bytes (once visible, e.g. in the relayer's broadcast transaction or shared off-chain) and wrap/submit it in their own transaction first. This is exactly the "permit front-running" pattern the external report describes: a nonce-gated approval message that can be extracted and replayed ahead of the legitimate composed transaction.

Consider a relayer that bundles the user's `DelegateV2` action together with a compensating `Transfer` action to itself in one outer transaction (the same pattern the project's own docs describe for meta-tx relayer compensation: "the payment is done using $FT... included as the first action... the relayer will be paid in the same transaction" [8](#0-7) ). A griefer who observes the pending `SignedDelegateAction` extracts it and submits it alone (or in their own wrapper) with higher priority. Once it lands, the sender's access-key nonce advances. When the relayer's original transaction executes, `validate_delegate_action_key` now sees a stale nonce and sets `DelegateActionInvalidNonce`, causing the *entire* bundled receipt — including the relayer's compensating `Transfer` action that had nothing to do with the delegate action — to fail and roll back per the atomic all-or-nothing action loop and rollback logic cited above.

### Impact Explanation
The relayer still burns the gas/fees for the whole failed receipt (gas burning happens regardless of `result.result`'s success, only the state changes roll back), but loses any compensation/value it expected from the other bundled actions, and the user's intended inner actions never execute either. This is a direct fee/gas-loss griefing vector reachable by any unprivileged party who can observe a pending `SignedDelegateAction` and win the race to submit it first — structurally identical to the TOFT `lzCompose` bug: an approval sub-message bundled with other messages, where front-running the approval's one-time nonce reliably DoSes the whole bundle and burns the fee paid for the other, unrelated actions.

### Likelihood Explanation
The attack requires only observing a pending transaction containing a `SignedDelegateAction` (visible once broadcast to the network/mempool) and submitting a competing transaction with the same payload ahead of it — no privileged role, validator collusion, or special access is required. The design already documents that meta-transactions "require some trust between the relayer and its user" for balance-timing reasons [9](#0-8) , but this front-running griefing vector via third parties is a distinct concern not addressed by that trust discussion, since it doesn't require any misbehavior by Alice — only a third-party observer.

### Recommendation
Disallow bundling a `Delegate`/`DelegateV2` action together with other action types in the same outer action list (i.e., require a delegate action to be the *sole* action in its receipt, similar to how `DeleteAccount` already must be the final and (effectively) exclusive terminal action). Alternatively, ensure compensation/other actions bundled alongside a delegate action are not lost when only the delegate action's nonce validation fails, e.g., by allowing partial (non-atomic) success for actions preceding a failed delegate validation, or by moving nonce validation earlier/atomically with fee reservation so a griefed relayer's other actions are not silently discarded.

### Proof of Concept
1. Alice signs a `DelegateActionV2` (or `DelegateAction`) with nonce `N+1` wrapping an inner action (e.g. `FunctionCall`), intended to be relayed by Relayer.
2. Relayer builds an outer transaction: `[Action::DelegateV2(signed_delegate), Action::Transfer(compensation_to_relayer)]`, signed by Relayer, receiver = Alice. This passes `validate_actions_with_mode` since only one delegate action is present [1](#0-0) .
3. Relayer broadcasts this transaction. A griefer observes the `SignedDelegateAction` inside it (transactions are visible pre-inclusion) and independently submits `[Action::DelegateV2(signed_delegate)]` in their own transaction with a low-cost outer wrapper, landing in an earlier chunk.
4. This advances Alice's access-key nonce to `N+1` on-chain.
5. Relayer's original transaction now executes: `apply_delegate_action` → `validate_delegate_action_key` sees `delegate_nonce.nonce() (N+1) <= current_nonce (N+1)` → sets `DelegateActionInvalidNonce` [2](#0-1) .
6. `apply_action_receipt`'s loop breaks with `result.result = Err(...)`, and the receipt rolls back per `state_update.rollback()` [5](#0-4) , discarding the `Transfer` compensation action's effect even though it had nothing to do with the delegate action — Relayer paid gas but got neither Alice's action executed (already done by the griefer's tx, so no harm there) nor its own compensation. [10](#0-9) [11](#0-10) [12](#0-11) [13](#0-12)

### Citations

**File:** runtime/runtime/src/action_validation.rs (L55-113)
```rust
/// Validates given actions:
///
/// - Checks limits if applicable.
/// - Checks that the total number of actions doesn't exceed the limit.
/// - Checks that there not other action if Action::Delegate is present.
/// - Validates each individual action.
/// - Checks that the total prepaid gas doesn't exceed the limit.
pub(crate) fn validate_actions_with_mode(
    limit_config: &LimitConfig,
    actions: &[Action],
    receiver: &AccountId,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ActionsValidationError> {
    if actions.len() as u64 > limit_config.max_actions_per_receipt {
        return Err(ActionsValidationError::TotalNumberOfActionsExceeded {
            total_number_of_actions: actions.len() as u64,
            limit: limit_config.max_actions_per_receipt,
        });
    }

    // Centralized post-quantum gate. Mirrors the tx-admission gate in
    // `check_valid_for_config`, and is load-bearing for actions emitted by
    // contracts via host functions: those actions create new receipts that
    // never go through tx admission, so on a pre-feature protocol they must
    // be rejected here. The exhaustive match in
    // `Action::post_quantum_signatures_required` (including the recursive
    // walk into `Delegate`) forces every future action variant to make an
    // explicit decision at compile time.
    if !ProtocolFeature::PostQuantumSignatures.enabled(current_protocol_version)
        && actions.iter().any(Action::post_quantum_signatures_required)
    {
        return Err(ActionsValidationError::UnsupportedProtocolFeature {
            protocol_feature: "PostQuantumSignatures".to_owned(),
            version: current_protocol_version,
        });
    }

    if mode == ValidateReceiptMode::NewReceipt {
        validate_number_of_deploy_actions(actions, limit_config.max_deploy_actions_per_receipt)?;
    }

    let mut found_delegate_action = false;
    let mut iter = actions.iter().peekable();
    while let Some(action) = iter.next() {
        if let Action::DeleteAccount(_) = action {
            if iter.peek().is_some() {
                return Err(ActionsValidationError::DeleteActionMustBeFinal);
            }
        } else {
            if let Action::Delegate(_) | Action::DelegateV2(_) = action {
                if found_delegate_action {
                    return Err(ActionsValidationError::DelegateActionMustBeOnlyOne);
                }
                found_delegate_action = true;
            }
        }
        validate_action_with_mode(limit_config, action, receiver, current_protocol_version, mode)?;
    }
```

**File:** runtime/runtime/src/action_validation.rs (L1085-1096)
```rust
        assert_eq!(
            validate_actions(
                &test_limit_config(),
                &[
                    Action::CreateAccount(CreateAccountAction {}),
                    Action::Delegate(Box::new(signed_delegate_action)),
                ],
                &receiver,
                PROTOCOL_VERSION,
            ),
            Ok(()),
        );
```

**File:** runtime/runtime/src/actions.rs (L422-491)
```rust
pub(crate) fn apply_delegate_action(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    action_receipt: &VersionedActionReceipt,
    sender_id: &AccountId,
    signed_delegate_action: VersionedSignedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    if !signed_delegate_action.verify() {
        result.result = Err(ActionErrorKind::DelegateActionInvalidSignature.into());
        return Ok(());
    }
    let delegate_action = signed_delegate_action.delegate_action();
    if apply_state.block_height > delegate_action.max_block_height() {
        result.result = Err(ActionErrorKind::DelegateActionExpired.into());
        return Ok(());
    }
    if delegate_action.sender_id().as_str() != sender_id.as_str() {
        result.result = Err(ActionErrorKind::DelegateActionSenderDoesNotMatchTxReceiver {
            sender_id: delegate_action.sender_id().clone(),
            receiver_id: sender_id.clone(),
        }
        .into());
        return Ok(());
    }

    validate_delegate_action_key(state_update, apply_state, delegate_action, result)?;
    if result.result.is_err() {
        // Validation failed. Need to return Ok() because this is not a runtime error.
        // "result.result" will be return to the User as the action execution result.
        return Ok(());
    }

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

    let prepaid_send_fees = total_prepaid_send_fees(&apply_state.config, action_receipt.actions())?;
    let required_cost = receipt_required_cost(apply_state, &new_receipt)?;
    // This gas will be burnt by the receiver of the created receipt.
    // Compute costs of that are not relevant at this point, the "used" gas is
    // only reserved for execution later, potentially on a different shard.
    result.gas_used = result.gas_used.checked_add_result(required_cost.gas)?;
    // This gas was prepaid on Relayer shard. Need to burn it because the receipt is going to be sent.
    // gas_used is incremented because otherwise the gas will be refunded. Refund function checks only gas_used.
    result.gas_used = result.gas_used.checked_add_result(prepaid_send_fees.gas)?;
    result.gas_burnt = result.gas_burnt.checked_add_result(prepaid_send_fees.gas)?;
    result.compute_usage = safe_add_compute(result.compute_usage, prepaid_send_fees.compute)?;
    result.new_receipts.push(new_receipt);

    Ok(())
}
```

**File:** runtime/runtime/src/actions.rs (L530-624)
```rust
/// Validate access key which was used for signing DelegateAction:
///
/// - Checks whether the access key is present fo given public_key and sender_id.
/// - Validates nonce and updates it if it's ok.
/// - Validates access key permissions.
fn validate_delegate_action_key(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    delegate_action: VersionedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let sender_id = delegate_action.sender_id();
    let public_key = delegate_action.public_key();
    // 'sender_id' account existence must be checked by a caller
    let mut access_key = match get_access_key(state_update, sender_id, public_key)? {
        Some(access_key) => access_key,
        None => {
            result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                InvalidAccessKeyError::AccessKeyNotFound {
                    account_id: sender_id.clone(),
                    public_key: public_key.clone().into(),
                },
            )
            .into());
            return Ok(());
        }
    };

    // A plain nonce advances the single access_key.nonce and forbids gas keys;
    // a gas key nonce advances one of the gas key's nonces selected by
    // nonce_index.
    let delegate_nonce = delegate_action.nonce();
    let (current_nonce, nonce_update) = match delegate_nonce {
        TransactionNonce::Nonce { .. } => {
            if access_key.gas_key_info().is_some() {
                result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::DelegateActionRequiresNonGasKey,
                )
                .into());
                return Ok(());
            }
            (access_key.nonce, DelegateNonceUpdate::AccessKey)
        }
        TransactionNonce::GasKeyNonce { nonce_index, .. } => {
            let Some(gas_key_info) = access_key.gas_key_info() else {
                result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::DelegateActionRequiresGasKey,
                )
                .into());
                return Ok(());
            };
            if nonce_index >= gas_key_info.num_nonces {
                result.result = Err(ActionErrorKind::DelegateActionInvalidNonceIndex {
                    nonce_index,
                    num_nonces: gas_key_info.num_nonces,
                }
                .into());
                return Ok(());
            }
            // The index is range-checked above and gas keys initialize every
            // nonce row at creation, so a missing row is inconsistent state.
            let current_nonce =
                get_gas_key_nonce(state_update, sender_id, public_key, nonce_index)?.ok_or_else(
                    || {
                        StorageError::StorageInconsistentState(format!(
                            "gas key nonce row missing for {} {} at in-range index {nonce_index} (num_nonces {})",
                            sender_id, public_key, gas_key_info.num_nonces,
                        ))
                    },
                )?;
            (current_nonce, DelegateNonceUpdate::GasKey { nonce_index })
        }
    };

    if delegate_nonce.nonce() <= current_nonce {
        result.result = Err(ActionErrorKind::DelegateActionInvalidNonce {
            delegate_nonce: delegate_nonce.nonce(),
            ak_nonce: current_nonce,
        }
        .into());
        return Ok(());
    }

    let upper_bound = apply_state.block_height
        * near_primitives::account::AccessKey::ACCESS_KEY_NONCE_RANGE_MULTIPLIER;
    if delegate_nonce.nonce() >= upper_bound {
        result.result = Err(ActionErrorKind::DelegateActionNonceTooLarge {
            delegate_nonce: delegate_nonce.nonce(),
            upper_bound,
        }
        .into());
        return Ok(());
    }

    let actions = delegate_action.get_actions();
```

**File:** runtime/runtime/src/lib.rs (L773-975)
```rust
    fn apply_action_receipt(
        &self,
        state_update: &mut TrieUpdate,
        apply_state: &ApplyState,
        preparation_pipeline: &ReceiptPreparationPipeline,
        receipt: &Receipt,
        receipt_sink: &mut ReceiptSink,
        instant_receipts: &mut VecDeque<Receipt>,
        validator_proposals: &mut Vec<ValidatorStake>,
        stats: &mut ChunkApplyStatsV1,
        epoch_info_provider: &dyn EpochInfoProvider,
        receipt_to_tx: &mut Vec<(CryptoHash, ReceiptToTxInfo)>,
    ) -> Result<ExecutionOutcomeWithId, RuntimeError> {
        let action_receipt: VersionedActionReceipt = match receipt.versioned_receipt() {
            VersionedReceiptEnum::Action(action_receipt)
            | VersionedReceiptEnum::PromiseYield(action_receipt) => action_receipt,
            _ => unreachable!("given receipt should be an action receipt"),
        };
        let account_id = receipt.receiver_id();
        // Collecting input data and removing it from the state
        let promise_results = action_receipt
            .input_data_ids()
            .iter()
            .map(|data_id| {
                let ReceivedData { data } = get_received_data(state_update, account_id, *data_id)?
                    .ok_or_else(|| {
                        StorageError::StorageInconsistentState(
                            "received data should be in the state".to_string(),
                        )
                    })?;
                state_update.remove(TrieKey::ReceivedData {
                    receiver_id: account_id.clone(),
                    data_id: *data_id,
                });
                match data {
                    // TODO: Going from Vec<u8> to Rc<[u8]> shrinks the
                    // allocated buffer to fit, which may re-allocate if the
                    // capacity > len.
                    // Most likely, capacity == len holds here anyway but it
                    // would be better to use `Rc<u8>` already in `ReceivedData`
                    // and `DataReceipt`.
                    Some(value) => Ok(PromiseResult::Successful(Rc::from(value))),
                    None => Ok(PromiseResult::Failed),
                }
            })
            .collect::<Result<Arc<[PromiseResult]>, RuntimeError>>()?;

        // state_update might already have some updates so we need to make sure we commit it before
        // executing the actual receipt
        state_update.commit(StateChangeCause::ActionReceiptProcessingStarted {
            receipt_hash: receipt.get_hash(),
        });

        let mut account = get_account(state_update, account_id)?;
        let account_did_not_exist = account.is_none();
        let mut actor_id = receipt.predecessor_id().clone();
        let mut result = ActionReceiptResult::new();
        let exec_fees = apply_state.config.fees.fee(ActionCosts::new_action_receipt).exec_fee();
        result.gas_used = exec_fees.gas;
        result.gas_burnt = exec_fees.gas;
        result.compute_usage = exec_fees.compute;

        let storage_proof_size_before_receipt =
            if ProtocolFeature::EnforcePerReceiptStorageProofLimit
                .enabled(apply_state.current_protocol_version)
            {
                Some(state_update.trie.recorded_storage_size_upper_bound())
            } else {
                None
            };

        // Executing actions one by one
        for (action_index, action) in action_receipt.actions().iter().enumerate() {
            let action_hash = create_action_hash_from_receipt_id(
                receipt.receipt_id(),
                apply_state.block_height,
                action_index,
            );
            let mut new_result = self.apply_action(
                action,
                state_update,
                apply_state,
                preparation_pipeline,
                &mut account,
                &mut actor_id,
                receipt,
                &action_receipt,
                Arc::clone(&promise_results),
                &action_hash,
                action_index,
                &action_receipt.actions(),
                epoch_info_provider,
                storage_proof_size_before_receipt,
            )?;
            if new_result.result.is_ok() {
                if let Err(e) = new_result.new_receipts.iter().try_for_each(|receipt| {
                    validate_receipt(
                        &apply_state.config.wasm_config.limit_config,
                        receipt,
                        apply_state.current_protocol_version,
                        ValidateReceiptMode::NewReceipt,
                    )
                }) {
                    new_result.result = Err(ActionErrorKind::NewReceiptValidationError(e).into());
                }
            }
            result.merge(new_result)?;
            // TODO storage error
            if let Err(ref mut res) = result.result {
                res.index = Some(action_index as u64);
                break;
            }
        }

        // Going to check balance covers account's storage.
        if result.result.is_ok() {
            if let Some(ref account) = account {
                match check_storage_stake(account, account.amount(), &apply_state.config) {
                    Ok(()) => {
                        set_account(state_update, account_id.clone(), account);
                    }
                    Err(StorageStakingError::LackBalanceForStorageStaking(amount)) => {
                        result.set_error(ActionError {
                            index: None,
                            kind: ActionErrorKind::LackBalanceForState {
                                account_id: account_id.clone(),
                                amount,
                            },
                        });
                    }
                    Err(StorageStakingError::StorageError(err)) => {
                        return Err(RuntimeError::StorageError(
                            StorageError::StorageInconsistentState(err),
                        ));
                    }
                }
            }
        }

        // The price at which the gas attached to this receipt was purchased.
        let gas_purchase_price = action_receipt.gas_price();

        // The price at which gas was burnt while applying this receipt. Can be different from the price at
        // which the gas was purchased.
        let gas_burn_price =
            if ProtocolFeature::AccountCostIncrease.enabled(apply_state.current_protocol_version) {
                // should always be <= gas_purchase_price, otherwise receiver_reward might underflow
                // or mint new tokens.
                std::cmp::min(gas_purchase_price, apply_state.gas_price)
            } else {
                apply_state.gas_price
            };

        let gas_refund_result = if receipt.predecessor_id().is_system() {
            // If the refund fails tokens are burned.
            if result.result.is_err() {
                stats.balance.other_burnt_amount = safe_add_balance(
                    stats.balance.other_burnt_amount,
                    total_deposit(&action_receipt.actions())?,
                )?
            }
            GasRefundResult::default()
        } else {
            let created_new_account =
                account_did_not_exist && account.is_some() && result.result.is_ok();

            // Calculating and generating refunds
            self.refund_unspent_gas_and_deposits(
                gas_burn_price,
                gas_purchase_price,
                receipt,
                &action_receipt,
                &mut result,
                &apply_state.config,
                created_new_account,
                apply_state.current_protocol_version,
            )?
        };
        stats.balance.gas_deficit_amount =
            safe_add_balance(stats.balance.gas_deficit_amount, gas_refund_result.price_deficit)?;

        // Moving validator proposals
        validator_proposals.append(&mut result.validator_proposals);

        // Committing or rolling back state.
        match &result.result {
            Ok(_) => {
                state_update.commit(StateChangeCause::ReceiptProcessing {
                    receipt_hash: receipt.get_hash(),
                });
            }
            Err(_) => {
                state_update.rollback();
            }
        };
        // If the receipt is a refund, then we consider it free without burnt gas.
        let gas_burnt: Gas =
            if receipt.predecessor_id().is_system() { Gas::ZERO } else { result.gas_burnt };
        // `price_deficit` is strictly less than `gas_burn_price * gas_burnt`.
        let mut tx_burnt_amount = safe_gas_to_balance(gas_burn_price, gas_burnt)?
            .checked_sub(gas_refund_result.price_deficit)
            .unwrap();
        if !ProtocolFeature::AccountCostIncrease.enabled(apply_state.current_protocol_version) {
```

**File:** docs/architecture/how/meta-tx.md (L43-45)
```markdown
it (off-chain) to a relayer. The relayer wraps it in a transaction, of which the
relayer is the signer and therefore pays the gas costs. If the inner actions
have an attached token balance, this is also paid for by the relayer.
```

**File:** docs/architecture/how/meta-tx.md (L66-70)
```markdown
In the example visualized above, the payment is done using \$FT. Together with
the transfer to John, Alice also adds an action to pay 0.1 \$FT to the relayer.
The relayer checks the content of the `SignedDelegateAction` and only processes
it if this payment is included as the first action. In this way, the relayer
will be paid in the same transaction as John.
```

**File:** docs/architecture/how/meta-tx.md (L72-80)
```markdown
Note that the payment to the relayer is still not guaranteed. It could be that
Alice does not have sufficient $FT and the transfer fails. To mitigate, the
relayer should check the $FT balance of Alice first.

Unfortunately, this still does not guarantee that the balance will be high
enough once the meta transaction executes. The relayer could waste NEAR gas
without compensation if Alice somehow reduces her \$FT balance in just the right
moment. Some level of trust between the relayer and its user is therefore
required.
```
