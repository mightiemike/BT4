### Title
`DelegateAction` `max_block_height` expiry check uses `>` instead of `>=`, allowing execution one block past user-intended TTL — (`runtime/runtime/src/actions.rs`)

### Summary

`apply_delegate_action` checks `apply_state.block_height > delegate_action.max_block_height()` to reject expired meta-transactions. Because the comparison is strict (`>`), a `DelegateAction` with `max_block_height = N` is accepted and executed at block height `N`, even though the protocol specification and field documentation state the action should be invalid when `block_height >= max_block_height`. A malicious or opportunistic relayer can hold a signed `DelegateAction` and submit it at the exact boundary block, executing the user's action one block past the user's intended authorization window.

### Finding Description

`DelegateAction.max_block_height` is documented as "The maximal height of the block in the blockchain **below which** the given DelegateAction is valid." The runtime spec (`docs/RuntimeSpec/Actions.md`) explicitly states the error condition as "If the current block is **equal or greater than** `max_block_height`" → `DelegateActionExpired`.

The implementation in `apply_delegate_action` reads:

```rust
if apply_state.block_height > delegate_action.max_block_height() {
    result.result = Err(ActionErrorKind::DelegateActionExpired.into());
    return Ok(());
}
``` [1](#0-0) 

The condition is `>` (strict), not `>=`. When `block_height == max_block_height`, the guard is not triggered and execution continues normally through `validate_delegate_action_key` and receipt creation.

The existing unit test `test_delegate_action_max_height` only verifies that `max_block_height + 1` causes failure; it uses `max_block_height` itself as the *success* case, confirming the off-by-one is present and untested:

```rust
// Setup current block as higher than max_block_height. Must fail.
let apply_state = create_apply_state(signed_delegate_action.delegate_action.max_block_height + 1);
``` [2](#0-1) 

The same `max_block_height()` accessor is shared by both `DelegateAction` (V1) and `DelegateActionV2` (V2), so both variants are affected. [3](#0-2) [4](#0-3) [5](#0-4) 

### Impact Explanation

A user signs a `DelegateAction` with `max_block_height = N` and hands it to a relayer, intending the authorization to expire before block `N`. Due to the off-by-one, the relayer can withhold the action and submit it at block `N`, causing the user's inner actions (transfers, function calls, stake changes, key additions/deletions) to execute one block past the user's intended authorization window. This constitutes an **unauthorized transaction**: the user's cryptographic authorization was scoped to blocks `< N`, but the runtime accepts it at block `= N`. The relayer pays gas; the user's account bears the deposit and any state changes from the inner actions.

### Likelihood Explanation

The `DelegateAction` / meta-transaction feature is live on mainnet. Any relayer who receives a signed `DelegateAction` can observe the `max_block_height` field and time submission to land at exactly that block height. Block production on NEAR is regular (~1 s/block), making precise timing straightforward. No special privilege is required beyond being the relayer for the action.

### Recommendation

Change the expiry check from strict greater-than to greater-than-or-equal in `apply_delegate_action`:

```diff
- if apply_state.block_height > delegate_action.max_block_height() {
+ if apply_state.block_height >= delegate_action.max_block_height() {
``` [1](#0-0) 

Update `test_delegate_action_max_height` to also assert that `block_height == max_block_height` produces `DelegateActionExpired`, and add a complementary test that `block_height == max_block_height - 1` succeeds.

### Proof of Concept

```rust
// In runtime/runtime/src/actions.rs tests:
#[test]
fn test_delegate_action_expires_at_max_block_height() {
    let mut result = ActionResult::default();
    let (action_receipt, signed_delegate_action) = create_delegate_action_receipt();
    let sender_id = signed_delegate_action.delegate_action.sender_id.clone();
    let sender_pub_key = signed_delegate_action.delegate_action.public_key.clone();
    let access_key = AccessKey { nonce: 19000000, permission: AccessKeyPermission::FullAccess };

    // block_height == max_block_height: should be DelegateActionExpired per spec,
    // but currently SUCCEEDS due to the off-by-one (> instead of >=).
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

    // This assertion FAILS with the current code (result is Ok, not Expired),
    // demonstrating the off-by-one vulnerability.
    assert_eq!(result.result, Err(ActionErrorKind::DelegateActionExpired.into()));
}
```

The test at `create_apply_state(max_block_height)` currently passes (action executes), whereas the spec requires it to return `DelegateActionExpired`. The relayer exploit path is: obtain a signed `DelegateAction` with `max_block_height = N`, wait until block `N`, submit — the action executes despite the user's intent that it be expired.

### Citations

**File:** runtime/runtime/src/actions.rs (L435-438)
```rust
    if apply_state.block_height > delegate_action.max_block_height() {
        result.result = Err(ActionErrorKind::DelegateActionExpired.into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L1358-1374)
```rust
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

**File:** core/primitives/src/action/delegate.rs (L60-64)
```rust
    /// The maximal height of the block in the blockchain below which the given DelegateAction is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
    pub public_key: PublicKey,
}
```

**File:** core/primitives/src/action/delegate.rs (L129-133)
```rust
    /// The maximal height of the block in the blockchain below which the given DelegateActionV2 is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
    pub public_key: PublicKey,
}
```

**File:** core/primitives/src/action/delegate.rs (L271-276)
```rust
    pub fn max_block_height(&self) -> BlockHeight {
        match self {
            VersionedDelegateActionRef::V1(delegate_action) => delegate_action.max_block_height,
            VersionedDelegateActionRef::V2(delegate_action) => delegate_action.max_block_height,
        }
    }
```
