This is exactly the finding: `WithdrawFromGasKey`, an action that moves NEAR balance out of an account's gas key back into the account, could until recently be executed via a `Delegate`/`DelegateV2` meta-transaction, which the codebase now explicitly guards against with `ProtocolFeature::RejectWithdrawFromGasKeyInDelegate`. This closely mirrors the "missing access modifier" bug class: a state-mutating, balance-moving action (`action_withdraw_from_gas_key`) was reachable through a path (`Delegate`) that bypassed the intended actor/permission gate (`check_actor_permissions`, which requires `actor_id == account_id` for `WithdrawFromGasKey`).

### Title
`WithdrawFromGasKey` reachable via `Delegate`/`DelegateV2` meta-transactions bypassed the actor-permission gate before `RejectWithdrawFromGasKeyInDelegate` - (File: runtime/runtime/src/actions.rs, runtime/runtime/src/access_keys.rs)

### Summary
`Action::WithdrawFromGasKey` moves balance from a gas key back to the owning account and is one of the "administrative" actions gated by `check_actor_permissions`, which requires `actor_id == account_id` before it may execute [1](#0-0) . However, prior to the `RejectWithdrawFromGasKeyInDelegate` protocol feature, this action could be nested inside a `Delegate`/`DelegateV2` action and relayed by a third party, and the test in `test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs` explicitly demonstrates that a relayer-submitted delegate containing `WithdrawFromGasKey` was admitted and drained the gas key pre-upgrade [2](#0-1) .

### Finding Description
The intended access-control model is that `WithdrawFromGasKey` is an "actor-only" action — the executing actor must equal the account itself — enforced in `check_actor_permissions` [3](#0-2) . The action itself simply reads the gas key, checks the balance, and moves funds into the account with no independent caller check inside `action_withdraw_from_gas_key` [4](#0-3) ; it relies entirely on the outer gate to establish that only the account itself (i.e., the holder of the underlying key) triggers it directly.

Comments in the VM import table explicitly document the intended invariant: `WithdrawFromGasKey` "must only be initiated via transactions, not by contracts" and there are deliberately no promise/host-function equivalents "to be visible to the pending transaction queue" per NEP-611 [5](#0-4) . The `Delegate`/`DelegateV2` path, however, was not subject to this same restriction — `apply_delegate_action` and `check_actor_permissions` treat `Delegate`/`DelegateV2` as pass-through wrappers with no actor check of their own [6](#0-5) , meaning the inner action executes with `actor_id` set to the delegating sender rather than being blocked by the "must be initiated via transaction, not indirectly" invariant. The regression test `test_reject_delegated_gas_key_withdraw_protocol_upgrade` confirms that, on the pre-upgrade protocol version, a relayer could submit a `Delegate` action whose inner action is `WithdrawFromGasKey`, and it would succeed and drain the sender's gas key balance [7](#0-6) .

This is the direct analog of the Sherlock report: a security-relevant, balance-moving action (`setPoolActive`/`WithdrawFromGasKey`) was documented/intended to require a specific caller path, but was reachable through an alternate route (any contract call/meta-transaction relayer, respectively) that the enforcing check did not cover — a classic "missing/incomplete access gate on a specific code path" bug.

### Impact Explanation
Before the fix (`RejectWithdrawFromGasKeyInDelegate`), a gas key's NEAR balance could be pulled back into the owning account via a relayed meta-transaction path that was supposed to be excluded from initiating this balance movement, and the test shows the balance was concretely decreased by the withdrawn amount [8](#0-7) . While the funds still land in the rightful owner's account balance (not stolen by an attacker) the point of the NEP-611 restriction is to keep gas-key-balance-reducing actions visible only via the direct pending-transaction queue so that pending transactions relying on that gas key's allowance are not silently invalidated out-of-band — bypassing that via delegation could allow a relayer to unexpectedly drain a gas key mid-flight of other pending gas-key transactions, causing unintended transaction failures/allowance exhaustion (a state-transition/behavior divergence from the documented invariant), which the protocol subsequently closed as a hard rule.

### Likelihood Explanation
This is highly reachable: any account holder can construct a `SignedDelegateAction` whose inner action is `WithdrawFromGasKey`, sign it with their own full-access/plain key (since gas keys cannot sign V1 delegate actions), and have any relayer submit it as a `Delegate`/`DelegateV2` action targeting themselves; no privileged position is required, exactly matching the required threat model (an unprivileged signer/relayer submitting a normal transaction).

### Recommendation
This is already fixed in the current codebase via `ProtocolFeature::RejectWithdrawFromGasKeyInDelegate`, which (per the test) causes delegated `WithdrawFromGasKey` actions to be rejected at/after the protocol version where the feature activates [9](#0-8) . For any codebase without this guard, `apply_delegate_action` (or an equivalent up-front action-shape validator) should explicitly reject inner `Action::WithdrawFromGasKey` (and any other action documented as "transaction-initiation only") before dispatch, rather than relying solely on `check_actor_permissions`'s `actor_id == account_id` equality, since a self-targeted delegate naturally satisfies that equality and defeats the intended restriction.

### Proof of Concept
The existing regression test constructs exactly this scenario: sender adds a gas key, funds it via `TransferToGasKey`, then builds a `DelegateAction` with `receiver_id == sender_id` and inner action `WithdrawFromGasKey`, signed by the sender's own signer and submitted by a distinct `relayer` account as `Action::Delegate` [10](#0-9) . On the pre-upgrade protocol version this transaction succeeds and the gas key balance decreases by `WITHDRAW_AMOUNT`, demonstrating the bypass [11](#0-10) .

### Citations

**File:** runtime/runtime/src/actions.rs (L755-776)
```rust
pub(crate) fn check_actor_permissions(
    action: &Action,
    account: &Option<Account>,
    actor_id: &AccountId,
    account_id: &AccountId,
) -> Result<(), ActionError> {
    match action {
        Action::DeployContract(_)
        | Action::Stake(_)
        | Action::AddKey(_)
        | Action::DeleteKey(_)
        | Action::DeployGlobalContract(_)
        | Action::UseGlobalContract(_)
        | Action::WithdrawFromGasKey(_) => {
            if actor_id != account_id {
                return Err(ActionErrorKind::ActorNoPermission {
                    account_id: account_id.clone(),
                    actor_id: actor_id.clone(),
                }
                .into());
            }
        }
```

**File:** runtime/runtime/src/actions.rs (L793-798)
```rust
        Action::CreateAccount(_)
        | Action::FunctionCall(_)
        | Action::Transfer(_)
        | Action::TransferToGasKey(_) => (),
        Action::Delegate(_) | Action::DelegateV2(_) => (),
        Action::DeterministicStateInit(_) | Action::UniversalStateInit(_) => (),
```

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L1-54)
```rust
use crate::setup::builder::TestLoopBuilder;
use crate::setup::env::TestLoopEnv;
use crate::tests::gas_keys::query_gas_key_and_balance;
use crate::utils::account::create_account_id;
use assert_matches::assert_matches;
use near_async::time::Duration;
use near_crypto::{InMemorySigner, KeyType, Signer};
use near_o11y::testonly::init_test_logger;
use near_primitives::account::AccessKey;
use near_primitives::action::delegate::{DelegateAction, SignedDelegateAction};
use near_primitives::action::{AddKeyAction, TransferToGasKeyAction, WithdrawFromGasKeyAction};
use near_primitives::errors::{ActionsValidationError, InvalidTxError};
use near_primitives::shard_layout::ShardLayout;
use near_primitives::test_utils::create_user_test_signer;
use near_primitives::transaction::{Action, SignedTransaction};
use near_primitives::types::{AccountId, Balance};
use near_primitives::upgrade_schedule::ProtocolUpgradeVotingSchedule;
use near_primitives::version::{MIN_SUPPORTED_PROTOCOL_VERSION, PROTOCOL_VERSION, ProtocolFeature};
use near_primitives::views::FinalExecutionStatus;

const WITHDRAW_AMOUNT: Balance = Balance::from_millinear(1);

/// Build a meta transaction whose inner action withdraws from the sender's own
/// gas key. The delegate is signed by the sender's plain access key, since a
/// gas key cannot sign a V1 delegate action.
fn delegated_withdraw_tx(
    env: &TestLoopEnv,
    sender: &AccountId,
    relayer: &AccountId,
    gas_key: &Signer,
) -> SignedTransaction {
    let sender_signer = create_user_test_signer(sender);
    let delegate_action = DelegateAction {
        sender_id: sender.clone(),
        receiver_id: sender.clone(),
        actions: vec![
            Action::WithdrawFromGasKey(Box::new(WithdrawFromGasKeyAction {
                public_key: gas_key.public_key(),
                amount: WITHDRAW_AMOUNT,
            }))
            .try_into()
            .unwrap(),
        ],
        nonce: env.rpc_node().get_next_nonce(sender),
        max_block_height: 1_000_000,
        public_key: sender_signer.public_key(),
    };
    let signed_delegate = SignedDelegateAction::sign(&sender_signer, delegate_action);
    env.rpc_node().tx_from_actions(
        relayer,
        sender,
        vec![Action::Delegate(Box::new(signed_delegate))],
    )
}
```

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L56-70)
```rust
#[test]
fn test_reject_delegated_gas_key_withdraw_protocol_upgrade() {
    init_test_logger();

    if !ProtocolFeature::RejectWithdrawFromGasKeyInDelegate.enabled(PROTOCOL_VERSION) {
        return;
    }

    let new_protocol = ProtocolFeature::RejectWithdrawFromGasKeyInDelegate.protocol_version();
    let old_protocol = new_protocol - 1;
    assert!(
        old_protocol >= MIN_SUPPORTED_PROTOCOL_VERSION,
        "no supported protocol version still admits a delegated WithdrawFromGasKey, so there is \
         nothing left to test here - remove this test"
    );
```

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L112-137)
```rust
    // Before the upgrade the nested withdrawal is admitted and moves balance out
    // of the gas key, which is the hole this rule closes.
    assert_eq!(
        env.rpc_node().protocol_version_at_head(),
        old_protocol,
        "expected to start pre-upgrade"
    );
    let (_, balance_before) =
        query_gas_key_and_balance(&env.rpc_node(), &sender, &gas_key.public_key());
    let tx = delegated_withdraw_tx(&env, &sender, &relayer, &gas_key);
    let outcome = env
        .rpc_runner()
        .execute_tx(tx, Duration::seconds(10))
        .expect("delegated withdrawal admitted pre-upgrade");
    assert_matches!(
        outcome.status,
        FinalExecutionStatus::SuccessValue(_),
        "pre-upgrade delegated withdrawal should execute",
    );
    let (_, balance_after) =
        query_gas_key_and_balance(&env.rpc_node(), &sender, &gas_key.public_key());
    assert_eq!(
        balance_after,
        balance_before.checked_sub(WITHDRAW_AMOUNT).unwrap(),
        "the nested withdrawal should have drained the gas key",
    );
```

**File:** runtime/runtime/src/access_keys.rs (L290-335)
```rust
pub(crate) fn action_withdraw_from_gas_key(
    state_update: &mut TrieUpdate,
    account: &mut Account,
    result: &mut ActionResult,
    account_id: &AccountId,
    action: &WithdrawFromGasKeyAction,
) -> Result<(), RuntimeError> {
    let Some(mut access_key) = get_access_key(state_update, account_id, &action.public_key)? else {
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };
    let Some(gas_key_info) = access_key.gas_key_info_mut() else {
        // Key exists but is not a gas key
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };

    let Some(updated_balance) = gas_key_info.balance.checked_sub(action.amount) else {
        result.result = Err(ActionErrorKind::InsufficientGasKeyBalance {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
            balance: gas_key_info.balance,
            required: action.amount,
        }
        .into());
        return Ok(());
    };
    gas_key_info.balance = updated_balance;
    set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);

    let new_account_balance = account.amount().checked_add(action.amount).ok_or_else(|| {
        RuntimeError::StorageError(StorageError::StorageInconsistentState(
            "Account balance integer overflow".to_string(),
        ))
    })?;
    account.set_amount(new_account_balance);
    Ok(())
}
```

**File:** runtime/near-vm-runner/src/imports.rs (L311-317)
```rust
    ] -> []>,
    // NOTE: There are intentionally no promise batch actions for
    // WithdrawFromGasKey. Actions that reduce gas key balance must only be
    // initiated via transactions, not by contracts. Otherwise, they will not be
    // visible to the pending transaction queue. Do not add host functions for
    // them. See NEP-611 for details.
    // #######################
```
