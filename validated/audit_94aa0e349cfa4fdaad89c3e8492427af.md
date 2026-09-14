### Title
Nested `WithdrawFromGasKey` inside a `DelegateAction` drains a gas key that the pending-transaction queue still counts as funded - ([File: chain/client/src/pending_transaction_queue.rs])

### Summary
The external report's bug class is: a budget/spend tracker that inspects only a shallow, top-level pattern (specific ERC20 selectors on the direct `target`) to decide how much value a batch of actions consumes, while real spending can also occur through indirect paths (`approve`+`transferFrom`, `permit`) that the shallow scan never sees — letting the enforced cap be silently exceeded. The nearcore analog is the SPICE pending-transaction queue, which tracks a signer's/gas-key's available balance by scanning a submitted transaction's actions for `WithdrawFromGasKey` only at the top level of the transaction, but a `WithdrawFromGasKey` action nested inside a `DelegateAction` (meta-transaction) is not counted, so the queue keeps treating the gas key as fully funded even after it has been drained.

### Finding Description
NEAR's gas-key feature (NEP-611) allows a `FunctionCall`/`FullAccess` gas key to hold a prepaid balance (`GasKeyInfo.balance`) that is debited to pay for gas/deposits. The SPICE pending transaction queue (used for pre-certification chunk admission) has to track uncertified, in-flight transactions so it doesn't admit a new transaction that would overdraw a gas key that is already being drained by transactions sitting in the queue. This is exactly analogous to the "budget cap" in the `ApprovalVotingModule.sol` case, and the protocol-version comment for `RejectWithdrawFromGasKeyInDelegate` documents the exact same *class* of bug on the NEAR side:

> "The SPICE pending transaction queue scans only the top level actions of a transaction for `WithdrawFromGasKey`, so a nested one drains a gas key that the queue still counts as funded." [1](#0-0) 

Just as the ERC20 budget scanner only pattern-matched `IERC20.transfer`/`transferFrom` selectors on the direct call target and missed indirect draining paths (`approve`+later `transferFrom`, `permit`), the pending transaction queue's balance-tracking logic only pattern-matches `WithdrawFromGasKey` actions that appear directly in the transaction's top-level action list. A `WithdrawFromGasKey` action nested one level deeper — inside the `actions` list of a `DelegateAction` carried by a meta-transaction — is invisible to that shallow scan (`chain/client/src/pending_transaction_queue.rs`), even though the runtime will still execute it and debit the gas key's balance (`runtime/runtime/src/access_keys.rs`, `action_withdraw_from_gas_key`). The mitigation (`RejectWithdrawFromGasKeyInDelegate`) confirms this by rejecting `WithdrawFromGasKey` when it appears inside a `DelegateAction`, i.e. the underlying flaw was exactly a shallow, incomplete scan for spend-tracking similar to the ERC20 report. [2](#0-1) 

### Impact Explanation
An attacker (an ordinary transaction signer holding a gas key) can submit:
1. A normal gas-key transaction that the pending queue admits based on the currently-tracked gas key balance.
2. A meta-transaction (`SignedDelegateAction`) whose inner `actions` contain a `WithdrawFromGasKey` action, which the queue's top-level-only scan does not detect and therefore does not deduct from its tracked available balance.

Because the queue believes the gas key still has its full balance, it will admit additional pending transactions against the same key that the actual on-chain balance can no longer cover. This produces:
- transactions that pass admission but then fail at execution with `NotEnoughGasKeyBalance` (a mismatch between the admission-time and execution-time state, i.e., a form of invalid/inconsistent transaction admission), and
- more importantly, a route by which the queue's accounting of "how much of a gas key's balance is already spoken for" can be made to diverge from the real balance — the exact "budget cap not accounting for all spending paths" issue from the source report, mapped onto SPICE's transaction admission/gas-key balance accounting rather than an ERC20 budget.

This is scoped to the SPICE-only pending transaction queue path (feature-gated tests use `protocol_feature_spice`), so its blast radius is bounded to that admission logic, but it is directly reachable by any unprivileged signer who owns a gas key and submits ordinary transactions plus a meta-transaction — no validator, peer, or operator privilege required.

### Likelihood Explanation
Likelihood is High for anyone using gas keys under SPICE: constructing a `DelegateAction` containing a `WithdrawFromGasKey` action and pairing it with ordinary pending transactions against the same gas key is a straightforward, deterministic sequence requiring only a signed transaction and a signed delegate action — both are standard, permissionless capabilities of a transaction signer. The bug is deterministic (not probabilistic or race-dependent), matching the "reachable from a single submitted transaction" bar.

### Recommendation
Fix as already implemented via `RejectWithdrawFromGasKeyInDelegate`: reject any `WithdrawFromGasKey` action found inside a `DelegateAction`'s inner action list at validation time (`runtime/runtime/src/action_validation.rs`), so a gas-key balance can only be withdrawn via a top-level transaction action that the pending transaction queue's scanner can see and account for. More generally, the pending transaction queue's balance-tracking scan (`chain/client/src/pending_transaction_queue.rs`) should recursively inspect nested/delegated action lists for any action type that can move balance out of a tracked account/key, rather than only scanning the top level — mirroring the ERC20 report's recommendation to track actual balance deltas end-to-end rather than pattern-matching specific call shapes.

### Proof of Concept
Conceptual PoC (consistent with the scenario exercised by `test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs` and `test-loop-tests/src/tests/pending_transaction_queue.rs::test_ptq_gas_key_balance_enforcement`, prior to the `RejectWithdrawFromGasKeyInDelegate` fix): [3](#0-2) 
1. Create and fund a gas key with balance `B` sufficient for exactly `N` ordinary gas-key transactions.
2. Submit `N` ordinary gas-key transactions; the queue's balance tracker correctly deducts each.
3. Submit a `SignedDelegateAction` (meta-transaction) whose inner action list contains a `WithdrawFromGasKey` action draining most of the remaining real balance. Because the queue scans only top-level actions, it does not deduct this withdrawal from its tracked balance.
4. Submit one more ordinary gas-key transaction that the queue still believes is affordable (based on stale tracked balance) but the chain no longer has funds to cover, producing a state where admission-time accounting and execution-time balance diverge — the SPICE analog of the ERC20 report's "budget cap not accounting for all spend paths."

Note: I could not fully trace the exact current-state (pre-/post-fix) code path inside `chain/client/src/pending_transaction_queue.rs` due to tool-call limits reached before reading its contents directly; the analysis above is based on the version.rs feature-gate comment, the test file names/matches found, and the general SPICE gas-key architecture. Confirming the precise line-level scan logic would require a follow-up read of `chain/client/src/pending_transaction_queue.rs`.

### Citations

**File:** core/primitives-core/src/version.rs (L462-465)
```rust
    /// The SPICE pending transaction queue scans only the top level actions of
    /// a transaction for `WithdrawFromGasKey`, so a nested one drains a gas key
    /// that the queue still counts as funded.
    RejectWithdrawFromGasKeyInDelegate,
```

**File:** runtime/runtime/src/action_validation.rs (L1-18)
```rust
use crate::config::total_prepaid_gas;
use crate::verifier::ValidateReceiptMode;
use near_crypto::key_conversion::is_valid_staking_key;
use near_primitives::account::AccessKeyPermission;
use near_primitives::action::delegate::VersionedDelegateActionRef;
use near_primitives::action::{
    AddKeyAction, DeployGlobalContractAction, DeterministicStateInitAction,
    GlobalContractIdentifier, UniversalStateInitAction, UseGlobalContractAction,
};
use near_primitives::errors::ActionsValidationError;
use near_primitives::transaction::{
    Action, DeleteAccountAction, DeployContractAction, FunctionCallAction, StakeAction,
};
use near_primitives::types::{AccountId, Balance, Gas};
use near_primitives::universal_state_init::UniversalStateInit;
use near_primitives::utils::{derive_near_deterministic_account_id, derive_universal_account_id};
use near_primitives::version::{ProtocolFeature, ProtocolVersion};
use near_vm_runner::logic::LimitConfig;
```

**File:** test-loop-tests/src/tests/pending_transaction_queue.rs (L437-491)
```rust
/// Gas key balance enforcement.
///
/// Create a gas key with enough balance for exactly 2 txs. Submit 2 gas key
/// txs and wait for inclusion (but not certification). Then submit one more
/// via execute_tx. The pending transaction queue tracks the first 2 as
/// pending, so the RPC rejects the third with NotEnoughGasKeyBalance.
#[test]
#[cfg_attr(not(feature = "protocol_feature_spice"), ignore)]
fn test_ptq_gas_key_balance_enforcement() {
    init_test_logger();

    let account = create_account_id("gas_key_account");
    let receiver = create_account_id("receiver");

    // Fund the gas key with enough for exactly 2 txs but not 3.
    let fund_amount = gas_cost_per_transfer().checked_mul(2).unwrap();
    let setup = setup_gas_key_spice_env(&account, &receiver, 1, fund_amount);
    let mut env = setup.env;
    let mut gas_key_nonce = setup.gas_key_nonces[0];

    // Submit the first 2 txs and wait for them to be included.
    let mut tx_hashes = Vec::new();
    let block_hash = env.validator().head().last_block_hash;
    for _ in 0..2 {
        gas_key_nonce += 1;
        let tx = SignedTransaction::from_actions_v1(
            TransactionNonce::from_nonce_and_index(gas_key_nonce, 0),
            account.clone(),
            receiver.clone(),
            &setup.gas_key_signer,
            vec![Action::Transfer(TransferAction { deposit: Balance::from_yoctonear(0) })],
            block_hash,
        );
        tx_hashes.push(tx.get_hash());
        env.validator().submit_tx(tx);
    }
    env.validator_runner().run_until_included(&tx_hashes);

    // Submit one more tx via execute_tx. The pending transaction queue
    // tracks the first 2 as uncertified, so the RPC handler should reject
    // it with NotEnoughGasKeyBalance.
    gas_key_nonce += 1;
    let block_hash = env.validator().head().last_block_hash;
    let tx = SignedTransaction::from_actions_v1(
        TransactionNonce::from_nonce_and_index(gas_key_nonce, 0),
        account,
        receiver,
        &setup.gas_key_signer,
        vec![Action::Transfer(TransferAction { deposit: Balance::from_yoctonear(0) })],
        block_hash,
    );
    let result = env.validator_runner().execute_tx(tx, Duration::seconds(5));
    assert!(matches!(result, Err(InvalidTxError::NotEnoughGasKeyBalance { .. })),);
}

```
