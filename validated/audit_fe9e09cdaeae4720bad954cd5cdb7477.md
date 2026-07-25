### Title
`DelegateAction` Expiry Off-by-One Allows Execution at `max_block_height` — (`runtime/runtime/src/actions.rs`)

### Summary

`apply_delegate_action` uses a strict `>` comparison to check whether a `DelegateAction` has expired, but the protocol specification and the `max_block_height` field description both state the action should be rejected when `block_height >= max_block_height`. An unprivileged relayer can therefore execute a signed meta-transaction at the exact boundary block the sender intended as the expiry, one block later than the user expected.

### Finding Description

In `apply_delegate_action`, the expiry guard is:

```rust
if apply_state.block_height > delegate_action.max_block_height() {
    result.result = Err(ActionErrorKind::DelegateActionExpired.into());
    return Ok(());
}
``` [1](#0-0) 

The `DelegateAction` field is documented as:

> "The maximal height of the block in the blockchain **below which** the given DelegateAction is valid." [2](#0-1) 

The protocol spec is equally explicit:

> "If the current block is **equal or greater than** `max_block_height`" → `DelegateActionExpired` [3](#0-2) 

Both sources require `>=`, but the code uses `>`. When `block_height == max_block_height` the guard is not triggered and the action proceeds to signature verification, nonce update, and receipt creation — executing the inner actions one block past the user-intended deadline.

The existing unit test only exercises `max_block_height + 1` (strictly greater) and explicitly asserts that `block_height == max_block_height` succeeds:

```rust
// Setup current block as higher than max_block_height. Must fail.
let apply_state = create_apply_state(signed_delegate_action.delegate_action.max_block_height + 1);
``` [4](#0-3) 

```rust
let apply_state = create_apply_state(signed_delegate_action.delegate_action.max_block_height);
// ...
assert!(result.result.is_ok(), ...);
``` [5](#0-4) 

The boundary case is never tested for rejection.

### Impact Explanation

A `DelegateAction` is a signed meta-transaction: the sender (`sender_id`) signs inner actions (transfers, function calls, etc.) and hands the signed payload to a relayer. The sender sets `max_block_height` as the deadline after which the relayer can no longer submit the action. If the sender wants to "cancel" a pending meta-transaction, they wait for the deadline block to pass.

Because the check is `>` instead of `>=`, the relayer can submit the action at exactly `block_height == max_block_height`. The inner actions — including `Transfer` actions that move funds — execute and produce receipts. The sender's account balance is debited at a block they believed was past the expiry. This constitutes an unauthorized transaction: the user's intent was that the action would be expired at that block height.

The `DelegateActionV2` variant (gas-key path) shares the same `max_block_height()` accessor and the same guard, so both meta-transaction variants are affected. [6](#0-5) 

### Likelihood Explanation

Any relayer holding a signed `DelegateAction` can exploit this by timing submission to land at exactly `block_height == max_block_height`. Block heights are public and predictable. No privileged access is required — only possession of the signed payload, which the sender must have already provided to the relayer. The window is one block, but block production is deterministic enough for a relayer to target it deliberately.

### Recommendation

Change the comparison from strict `>` to `>=` to match the specification:

```rust
// Before (off-by-one):
if apply_state.block_height > delegate_action.max_block_height() {

// After (correct):
if apply_state.block_height >= delegate_action.max_block_height() {
``` [1](#0-0) 

Add a unit test asserting that `block_height == max_block_height` produces `DelegateActionExpired`, mirroring the existing `test_delegate_action_max_height` test which only covers `max_block_height + 1`.

### Proof of Concept

1. Alice signs a `DelegateAction` with `max_block_height = H` containing `Transfer { deposit: 100 NEAR }` to Bob.
2. Alice hands the signed payload to a relayer, then decides to cancel by waiting for block `H`.
3. At block `H`, Alice believes the action is expired.
4. The relayer submits the action at block `H` (i.e., `apply_state.block_height == H`).
5. The guard `H > H` evaluates to `false` — no expiry error is raised.
6. `validate_delegate_action_key` runs, the nonce is advanced, and a new receipt is created transferring 100 NEAR to Bob.
7. Alice's balance is debited at the block she intended as the expiry deadline.

### Citations

**File:** runtime/runtime/src/actions.rs (L435-438)
```rust
    if apply_state.block_height > delegate_action.max_block_height() {
        result.result = Err(ActionErrorKind::DelegateActionExpired.into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L1289-1303)
```rust
        let apply_state =
            create_apply_state(signed_delegate_action.delegate_action.max_block_height);
        let mut state_update = setup_account(&sender_id, &sender_pub_key, &access_key);

        apply_delegate_action(
            &mut state_update,
            &apply_state,
            &VersionedActionReceipt::from(&action_receipt),
            &sender_id,
            (&signed_delegate_action).into(),
            &mut result,
        )
        .expect("Expect ok");

        assert!(result.result.is_ok(), "Result error: {:?}", result.result.err());
```

**File:** runtime/runtime/src/actions.rs (L1358-1360)
```rust
        // Setup current block as higher than max_block_height. Must fail.
        let apply_state =
            create_apply_state(signed_delegate_action.delegate_action.max_block_height + 1);
```

**File:** chain/jsonrpc/openapi/openapi.json (L4429-4434)
```json
          "max_block_height": {
            "description": "The maximal height of the block in the blockchain below which the given DelegateActionV2 is valid.",
            "format": "uint64",
            "minimum": 0,
            "type": "integer"
          },
```

**File:** docs/RuntimeSpec/Actions.md (L402-407)
```markdown
- If the current block is equal or greater than `max_block_height`

```rust
/// Delegate action has expired
DelegateActionExpired
```
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
