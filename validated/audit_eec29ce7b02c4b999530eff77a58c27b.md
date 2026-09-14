### Title
Nested `WithdrawFromGasKeyAction` inside a `DelegateAction` allows draining a gas key's balance without the gas key's own signature — (File: `runtime/runtime/src/access_keys.rs`, `runtime/runtime/src/action_validation.rs`)

### Summary
NEAR's `WithdrawFromGasKey` action is intended to let the holder of a gas key move balance out of it, but historically it could be embedded as an inner action of a `DelegateAction` (meta-transaction), letting the *account owner's regular access key* authorize the withdrawal on behalf of a gas key that never itself signed anything. This is analogous to the reported `sweepToken()` issue: a value-moving operation reachable without the specific credential/permission that should gate it.

### Finding Description
`WithdrawFromGasKeyAction` moves balance from a `GasKey` back to the owning account, and is validated/executed in `runtime/runtime/src/access_keys.rs`. A `DelegateAction` (meta-transaction, NEP-366) lets a sender sign a bundle of inner actions with their *plain* access key and have a relayer submit it; the receiver processes the inner actions as if the sender itself issued them directly ` [1](#0-0) `. Prior to a dedicated protocol guard, a `WithdrawFromGasKeyAction` nested inside such a delegate action was accepted and successfully drained the target gas key's funded balance, even though the transaction was authorized by the account's ordinary access key rather than by the gas key itself: ` [2](#0-1) `.

The protocol subsequently closed this specific hole with the `RejectWithdrawFromGasKeyInDelegate` feature gate in `action_validation.rs`, which rejects delegated transactions containing a nested `WithdrawFromGasKeyAction` with `ActionsValidationError::WithdrawFromGasKeyNotAllowedInDelegate` ` [3](#0-2) `. The test explicitly demonstrates that **before** the protocol upgrade, the nested withdrawal is admitted and moves balance out of the gas key — described in the test's own comment as "the hole this rule closes" ` [4](#0-3) `.

### Impact Explanation
Where this fix is not yet active (any network/build running a protocol version prior to `RejectWithdrawFromGasKeyInDelegate`, or any future action type that reuses gas-key/limited-authority withdrawal semantics without equivalent delegate-action gating), an attacker who can get any account's *regular* access key to co-sign a delegate action (or who controls a relayer that forwards attacker-crafted delegate actions) could drain balance out of a `GasKey` that was only ever meant to be spendable by whoever holds that specific gas key — a clear "unauthorized value movement" bypassing the intended withdrawal authorization. This matches the report's core defect class: a fund-moving entry point reachable by a party who should not have the authority to trigger it.

### Likelihood Explanation
Reaching this path only requires submitting an ordinary `DelegateAction`-carrying transaction — a standard, permissionless meta-transaction feature usable by any transaction signer/relayer, with no special privileges needed beyond having their own access key. The only thing preventing exploitation on current protocol versions is the specific `RejectWithdrawFromGasKeyInDelegate` validation added at admission time; without it (older protocol version) the underlying runtime happily processes the nested withdrawal, as proven by the pre-upgrade assertions in the referenced test.

### Recommendation
Ensure `RejectWithdrawFromGasKeyInDelegate` (or equivalent validation) is enforced on all supported protocol versions and mainnet/testnet deployments before this feature activates, and audit all other gas-key-authority-gated actions (e.g. `WithdrawFromGasKeyAction`, any future full-access/gas-key exclusive actions) for the same "nested-in-delegate-action" bypass pattern, rejecting them at transaction/delegate-action validation time (`runtime/runtime/src/action_validation.rs`) rather than relying only on execution-time checks.

### Proof of Concept
The existing regression test itself constitutes the PoC:
1. Create account `sender`; add a `GasKey` full-access key to it (`AddKeyAction` with `AccessKey::gas_key_full_access`).
2. Fund the gas key via `TransferToGasKeyAction`.
3. On a protocol version *before* `RejectWithdrawFromGasKeyInDelegate`, construct a `DelegateAction` signed by `sender`'s plain access key, whose sole inner action is `WithdrawFromGasKeyAction { public_key: gas_key.public_key(), amount: WITHDRAW_AMOUNT }`, and have a `relayer` submit it as `Action::Delegate`.
4. Observe the transaction succeeds and the gas key balance decreases by `WITHDRAW_AMOUNT`, confirming funds were moved out of the gas key without the gas key itself ever having signed the withdrawal — ` [5](#0-4) `.
5. On protocol versions with the fix, the identical delegate action is rejected at admission with `ActionsValidationError::WithdrawFromGasKeyNotAllowedInDelegate` — ` [6](#0-5) `, confirming the vulnerability existed and was fixed by this specific gate, not by any earlier general-purpose check.

### Citations

**File:** docs/architecture/how/meta-tx.md (L40-52)
```markdown
With meta transactions, Alice can create a `DelegateAction`, which is very
similar to a transaction. It also contains a list of actions to execute and a
single receiver for those actions. She signs the `DelegateAction` and forwards
it (off-chain) to a relayer. The relayer wraps it in a transaction, of which the
relayer is the signer and therefore pays the gas costs. If the inner actions
have an attached token balance, this is also paid for by the relayer.

On chain, the `SignedDelegateAction` inside the transaction is converted to an
action receipt with the same `SignedDelegateAction` on the relayer's shard. The
receipt is forwarded to the account from `Alice`, which will unpacked the
`SignedDelegateAction` and verify that it is signed by Alice with a valid Nonce
etc. If all checks are successful, a new action receipt with the inner actions
as body is sent to `FT`. There, the `ft_transfer` call finally executes.
```

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L22-25)
```rust

/// Build a meta transaction whose inner action withdraws from the sender's own
/// gas key. The delegate is signed by the sender's plain access key, since a
/// gas key cannot sign a V1 delegate action.
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

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L155-171)
```rust
    // After the upgrade the meta transaction is rejected at admission.
    assert!(
        ProtocolFeature::RejectWithdrawFromGasKeyInDelegate
            .enabled(env.rpc_node().protocol_version_at_head())
    );
    let tx = delegated_withdraw_tx(&env, &sender, &relayer, &gas_key);
    let err = env
        .rpc_runner()
        .execute_tx(tx, Duration::seconds(10))
        .expect_err("delegated withdrawal should be rejected post-upgrade");
    assert_matches!(
        err,
        InvalidTxError::ActionsValidation(
            ActionsValidationError::WithdrawFromGasKeyNotAllowedInDelegate
        ),
        "post-upgrade delegated withdrawal should be rejected with the new error, got {err:?}",
    );
```
