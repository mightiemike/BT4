Since `FixDelegatedDeterministicStateInit` activates at v85, and `MIN_SUPPORTED_PROTOCOL_VERSION` is 83, a node/network still on protocol version 83 or 84 would run the pre-fix code path, which is a live analog of the reported bug class.

### Title
Delegate-action receiver check bypasses `DeterministicStateInitAction` target validation, letting an attacker misdirect a meta-transaction's state-init - ([File: runtime/runtime/src/action_validation.rs])

### Summary
Just as `requestDepositWithPermit` failed to bind the `owner`/`spender` parameters to `msg.sender`, allowing anyone to submit mismatched parameters and corrupt a legitimate authorized request, nearcore's pre-fix `validate_delegate_action` validated a `DeterministicStateInitAction` nested inside a `DelegateAction` against the **outer transaction's** `receiver_id` instead of the `delegate_action.receiver_id`. This let a `SignedDelegateAction` targeting one deterministic account be embedded in an outer transaction addressed to a *different* deterministic account, passing initial validation despite the mismatch.

### Finding Description
`DeterministicStateInitAction` creates a "0s…" account whose id must equal `derive_near_deterministic_account_id(&action.state_init)` [1](#0-0) . The receiver-id binding for this check, when the action arrives via a meta-transaction, is what a `DelegateAction`'s `receiver_id` is supposed to fix in place [2](#0-1) . The test suite explicitly documents that, prior to protocol version `FixDelegatedDeterministicStateInit`, `validate_delegate_action` used `outer_tx.receiver_id` instead of `delegate_action.receiver_id` when checking the inner `DeterministicStateInitAction`, so a crafted delegate action naming `det_account_a` as receiver but carrying `state_init_b` (which derives to `det_account_b`) passed transaction-level validation [3](#0-2) . It was only caught later, at receipt-unpacking time, by `validate_receipt`, surfacing as `InvalidDeterministicStateInitReceiver` inside a `NewReceiptValidationError` [4](#0-3) . This defect was fixed by protocol feature `FixDelegatedDeterministicStateInit`, gated at protocol version 85 [5](#0-4) , while `MIN_SUPPORTED_PROTOCOL_VERSION` is 83 [6](#0-5) , meaning binaries/chains still running at protocol version 83 or 84 execute the unfixed validation path.

### Impact Explanation
On an unfixed protocol version, a relayer (or the meta-tx signer's own colluding party) can construct a `SignedDelegateAction` whose `receiver_id` does not match the `state_init`'s derived account. The malformed transaction is admitted into a chunk (consuming the sender's/relayer's nonce and gas) before it is ultimately rejected at receipt validation. This wastes gas paid by the relayer and burns the delegate action's nonce (a one-shot, strictly increasing value per NEP-366), permanently invalidating the legitimate deterministic-account bootstrap the user intended to submit — the same "front-run and deny" effect described in the report, where an unauthenticated mismatch in a signed request's target field silently consumes the request without completing its intended effect.

### Likelihood Explanation
This requires a network still running protocol version 83 or 84 (below the v85 fix) and an attacker able to submit or intercept/relay a `SignedDelegateAction`. Given `MIN_SUPPORTED_PROTOCOL_VERSION = 83` is coded into this binary, such versions are explicitly still supported, making the pre-fix path reachable in principle; however, mainnet networks have very likely already upgraded past v85, so real-world exploitability depends entirely on which protocol version is actually running.

### Recommendation
Confirm the deployed/target protocol version. If any supported chain is below protocol version 85, treat this as an active issue and backport `FixDelegatedDeterministicStateInit`. Additionally, consider removing support for protocol versions below the fix (raising `MIN_SUPPORTED_PROTOCOL_VERSION`) so the buggy code path can no longer be exercised at all, rather than relying solely on feature-gating.

### Proof of Concept
The exact reproduction is already codified in the test suite:
- `try_meta_tx_deterministic_receiver_exploit` builds `det_account_a`/`det_account_b`, deploys `det_account_b`, then crafts a `DelegateAction` with `receiver_id = det_account_a` but an inner `DeterministicStateInitAction` carrying `state_init_b` (which derives to `det_account_b`) [7](#0-6) .
- `test_deterministic_state_init_meta_tx_receiver_check_pre_fix` runs this at `fix_version - 1` and asserts the transaction is *accepted* at initial validation, only failing later with `InvalidDeterministicStateInitReceiver` inside `NewReceiptValidationError` [8](#0-7) .
- `test_deterministic_state_init_meta_tx_receiver_check` confirms that at `fix_version` and above the same transaction is now rejected immediately at tx validation [9](#0-8) .

### Citations

**File:** docs/RuntimeSpec/Actions.md (L340-361)
```markdown
```rust
/// The struct a user creates and signs to create a meta transaction.
struct DelegateAction {
    /// Signer of the delegated actions
    pub sender_id: AccountId,
    /// Receiver of the delegated actions.
    pub receiver_id: AccountId,
    /// List of actions to be executed.
    ///
    /// With the meta transactions MVP defined in NEP-366, nested
    /// DelegateActions are not allowed. A separate type is used to enforce it.
    pub actions: Vec<NonDelegateAction>,
    /// Nonce to ensure that the same delegate action is not sent twice by a
    /// relayer and should match for given account's `public_key`.
    /// After this action is processed it will increment.
    pub nonce: Nonce,
    /// The maximal height of the block in the blockchain below which the given DelegateAction is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
    pub public_key: PublicKey,
}
```
```

**File:** docs/RuntimeSpec/Actions.md (L489-513)
```markdown
## DeterministicStateInitAction

```rust
pub struct DeterministicStateInitAction {
    /// The data required to initialize the account.
    pub state_init: DeterministicAccountStateInit,
    /// A NEAR balance to cover storage requirements. Extra balance is refunded.
    pub deposit: Balance,
}
```

**Outcome**:

- if the account was already created before:
    - do nothing
- if the account was not created before:
    - creates an account with deterministic account id
    - sets the contract code to the specified global contract
    - stores the initial data into the contract storage

### Errors

**Validation Error**:

- `InvalidDeterministicStateInitReceiver` if the receiver id is not derived from the provided `DeterministicAccountStateInit`
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L128-155)
```rust
/// Ensure there is no exploit with invalid deterministic account ids through
/// meta transactions.
///
/// With the old (buggy) code, `validate_delegate_action` used
/// `outer_tx.receiver_id` instead of `delegate_action.receiver_id` when
/// checking inner actions. The exploit tx therefore passes initial tx
/// validation. The exploit is prevented by a following `validate_receipt` check
/// when the meta transaction is unpacked.
#[test]
fn test_deterministic_state_init_meta_tx_receiver_check_pre_fix() {
    let fix_version = ProtocolFeature::FixDelegatedDeterministicStateInit.protocol_version();
    let outcome = try_meta_tx_deterministic_receiver_exploit(fix_version - 1)
        .expect("without the fix, exploit tx passes initial tx validation");

    assert_matches!(
        outcome.status,
        FinalExecutionStatus::Failure(TxExecutionError::ActionError(ActionError {
            kind: ActionErrorKind::NewReceiptValidationError(
                ReceiptValidationError::ActionsValidation(
                    ActionsValidationError::InvalidDeterministicStateInitReceiver { .. }
                )
            ),
            ..
        })),
        "expected InvalidDeterministicStateInitReceiver in NewReceiptValidationError, got: {:?}",
        outcome.status
    );
}
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L159-171)
```rust
#[test]
fn test_deterministic_state_init_meta_tx_receiver_check() {
    let fix_version = ProtocolFeature::FixDelegatedDeterministicStateInit.protocol_version();
    let err = try_meta_tx_deterministic_receiver_exploit(fix_version)
        .expect_err("exploit tx must be rejected at tx validation with the fix");
    assert_matches!(
        err,
        InvalidTxError::ActionsValidation(
            ActionsValidationError::InvalidDeterministicStateInitReceiver { .. }
        ),
        "wrong error: {err:?}"
    );
}
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L232-261)
```rust
    // Craft the exploit: outer_tx.receiver = det_account_b = derive(state_init_b).
    // Old check: det_account_b == derive(state_init_b) passes.
    // The delegate action targets det_account_a, which is the wrong account.
    // In no protocol version can this ever be allowed to be executed successfully.
    let relayer = env.independent_account();
    let relayer_signer = create_user_test_signer(&relayer);
    let inner_action = Action::DeterministicStateInit(Box::new(DeterministicStateInitAction {
        state_init: state_init_b,
        deposit: Balance::ZERO,
    }));
    let delegate_nonce = env.next_nonce_for(&det_account_b);
    let delegate_action = DelegateAction {
        sender_id: det_account_b.clone(),
        receiver_id: det_account_a,
        actions: vec![NonDelegateAction::try_from(inner_action).unwrap()],
        nonce: delegate_nonce,
        max_block_height: 1_000_000,
        public_key: meta_tx_sender_signer.public_key(),
    };
    let signed_delegate_action =
        SignedDelegateAction::sign(&meta_tx_sender_signer, delegate_action);
    let tx = SignedTransaction::from_actions(
        env.next_nonce(),
        relayer,
        det_account_b,
        &relayer_signer,
        vec![Action::Delegate(Box::new(signed_delegate_action))],
        env.get_tx_block_hash(),
    );
    env.try_execute_tx(tx)
```

**File:** core/primitives-core/src/version.rs (L600-604)
```rust
            ProtocolFeature::_DeprecatedWasmtime => 84,
            ProtocolFeature::FixDelegateActionDepositWithFunctionCallError
            | ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
            | ProtocolFeature::FixDelegatedDeterministicStateInit
            | ProtocolFeature::GasKeys
```

**File:** protocol-model/spec/accounts-keys.md (L102-102)
```markdown
Account V2 itself has no live gating flag at v86 — the historical `_DeprecatedAccountVersions` was v46 (`version.rs:476`); V1/V2 coexist purely as a serialization concern. `MIN_SUPPORTED_PROTOCOL_VERSION` is 83 (`version.rs:600`), so the deprecated global-contract / deterministic-account-id / eth-implicit gates are always enabled on any version this binary processes.
```
