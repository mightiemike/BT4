### Title
`DelegateAction` Expiry Off-by-One: `>` Used Instead of `>=` in `apply_delegate_action` — (File: `runtime/runtime/src/actions.rs`)

---

### Summary

`apply_delegate_action` checks `apply_state.block_height > delegate_action.max_block_height()` to decide whether a meta-transaction has expired. The protocol specification and the field's own doc-comment both state the action should be rejected when `block_height >= max_block_height`. The `>` operator allows execution at the exact boundary block, giving a malicious relayer one extra block to submit a DelegateAction the user intended to have already expired.

---

### Finding Description

`DelegateAction.max_block_height` is documented as:

> "The maximal height of the block in the blockchain **below which** the given DelegateAction is valid." [1](#0-0) 

The runtime spec reinforces this:

> "If the current block is **equal or greater than** `max_block_height`" → `DelegateActionExpired` [2](#0-1) 

The actual enforcement in `apply_delegate_action` is:

```rust
if apply_state.block_height > delegate_action.max_block_height() {
    result.result = Err(ActionErrorKind::DelegateActionExpired.into());
    return Ok(());
}
``` [3](#0-2) 

The operator is `>` (strictly greater than), not `>=`. When `block_height == max_block_height` the condition is `false`, so the action proceeds instead of being rejected. The existing test only verifies the `max_block_height + 1` case and explicitly passes `max_block_height` as a valid block height in the success test: [4](#0-3) [5](#0-4) 

The same `apply_delegate_action` function handles both `Action::Delegate` and `Action::DelegateV2` through the `VersionedSignedDelegateActionRef` abstraction, so both variants are affected. [6](#0-5) 

---

### Impact Explanation

A user signs a `DelegateAction` with `max_block_height = N` intending the meta-transaction to be executable only for blocks strictly less than N. A malicious relayer can withhold the signed action and submit it at block N — one block past the user's intended deadline — and the runtime will accept it. The user's access key nonce is then advanced and the inner actions execute, violating the user's intended authorization window. This constitutes **contract execution flow breakage** and an **unauthorized transaction** (the user's signed intent was that the action must not execute at or after block N).

Concrete scenario:

1. User signs `DelegateAction { max_block_height: N, nonce: k, actions: [Transfer 100 NEAR to Bob] }` and hands it to a relayer.
2. User later decides to cancel by revoking the key or by relying on the expiry — they believe the action is dead at block N.
3. Relayer submits the action at block N. The check `N > N` is `false`, so the action is not expired and executes, transferring 100 NEAR.

---

### Likelihood Explanation

Likelihood is **low**. Exploitation requires a relayer who deliberately times submission to the exact boundary block. However, the trigger is fully unprivileged: any relayer holding a signed `DelegateAction` can attempt this without any special role. The window is one block (~1 second on NEAR mainnet), which is narrow but deterministic and automatable.

---

### Recommendation

Change the comparison operator from `>` to `>=`:

```rust
// Before (incorrect — allows execution at the boundary block)
if apply_state.block_height > delegate_action.max_block_height() {

// After (correct — rejects at the boundary block per spec)
if apply_state.block_height >= delegate_action.max_block_height() {
``` [7](#0-6) 

Add a boundary test that asserts `DelegateActionExpired` when `block_height == max_block_height`, mirroring the existing `test_delegate_action_max_height` test which only covers `max_block_height + 1`. [8](#0-7) 

---

### Proof of Concept

Using the existing test harness in `runtime/runtime/src/actions.rs`:

```rust
#[test]
fn test_delegate_action_expires_at_max_block_height() {
    let mut result = ActionResult::default();
    let (action_receipt, signed_delegate_action) = create_delegate_action_receipt();
    let sender_id = signed_delegate_action.delegate_action.sender_id.clone();
    let sender_pub_key = signed_delegate_action.delegate_action.public_key.clone();
    let access_key = AccessKey { nonce: 19000000, permission: AccessKeyPermission::FullAccess };

    // block_height == max_block_height: spec says this must be rejected
    let apply_state =
        create_apply_state(signed_delegate_action.delegate_action.max_block_height);
    let mut state_update = setup_account(&sender_id, &sender_pub_key, &access_key);

    apply_delegate_action(
        &mut state_update,
        &apply_state,
        &VersionedActionReceipt::from(action_receipt),
        &sender_id,
        (&signed_delegate_action).into(),
        &mut result,
    )
    .expect("Expect ok");

    // This assertion FAILS with the current code (result is Ok, not Expired)
    assert_eq!(result.result, Err(ActionErrorKind::DelegateActionExpired.into()));
}
```

The test fails on the current code because `apply_state.block_height > max_block_height` evaluates to `false` at the boundary, allowing the action to proceed. Changing `>` to `>=` makes the test pass and aligns the implementation with the protocol specification.

### Citations

**File:** core/primitives/src/action/delegate.rs (L60-61)
```rust
    /// The maximal height of the block in the blockchain below which the given DelegateAction is valid.
    pub max_block_height: BlockHeight,
```

**File:** docs/RuntimeSpec/Actions.md (L402-407)
```markdown
- If the current block is equal or greater than `max_block_height`

```rust
/// Delegate action has expired
DelegateActionExpired
```
```

**File:** runtime/runtime/src/actions.rs (L435-438)
```rust
    if apply_state.block_height > delegate_action.max_block_height() {
        result.result = Err(ActionErrorKind::DelegateActionExpired.into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L1289-1291)
```rust
        let apply_state =
            create_apply_state(signed_delegate_action.delegate_action.max_block_height);
        let mut state_update = setup_account(&sender_id, &sender_pub_key, &access_key);
```

**File:** runtime/runtime/src/actions.rs (L1351-1374)
```rust
    fn test_delegate_action_max_height() {
        let mut result = ActionResult::default();
        let (action_receipt, signed_delegate_action) = create_delegate_action_receipt();
        let sender_id = signed_delegate_action.delegate_action.sender_id.clone();
        let sender_pub_key = signed_delegate_action.delegate_action.public_key.clone();
        let access_key = AccessKey { nonce: 19000000, permission: AccessKeyPermission::FullAccess };

        // Setup current block as higher than max_block_height. Must fail.
        let apply_state =
            create_apply_state(signed_delegate_action.delegate_action.max_block_height + 1);
        let mut state_update = setup_account(&sender_id, &sender_pub_key, &access_key);

        apply_delegate_action(
            &mut state_update,
            &apply_state,
            &VersionedActionReceipt::from(action_receipt),
            &sender_id,
            (&signed_delegate_action).into(),
            &mut result,
        )
        .expect("Expect ok");

        assert_eq!(result.result, Err(ActionErrorKind::DelegateActionExpired.into()));
    }
```

**File:** runtime/runtime/src/lib.rs (L725-745)
```rust
            Action::Delegate(signed_delegate_action) => {
                metrics::ACTION_CALLED_COUNT.delegate.inc();
                apply_delegate_action(
                    state_update,
                    apply_state,
                    action_receipt,
                    account_id,
                    signed_delegate_action.as_ref().into(),
                    &mut result,
                )?;
            }
            Action::DelegateV2(signed_delegate_action) => {
                metrics::ACTION_CALLED_COUNT.delegate.inc();
                apply_delegate_action(
                    state_update,
                    apply_state,
                    action_receipt,
                    account_id,
                    signed_delegate_action.as_ref().into(),
                    &mut result,
                )?;
```
