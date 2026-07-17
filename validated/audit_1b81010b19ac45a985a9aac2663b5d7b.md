### Title
`DelegateAction` Expiry Boundary Off-by-One: Spec Requires `>=` but Runtime Enforces `>` — (`File: runtime/runtime/src/actions.rs`)

### Summary

The `DelegateAction` (meta-transaction) expiry check in `apply_delegate_action` uses a strict-greater-than comparison (`block_height > max_block_height`), but the protocol specification, the field docstring, and the `DelegateActionV2` field docstring all state the action is valid only for blocks **below** `max_block_height` — meaning it should expire **at** `max_block_height` (i.e., `>=`). At exactly `block_height == max_block_height`, the spec says the action is expired, but the runtime accepts it. A malicious relayer who holds a signed `DelegateAction` can execute it at the exact block the user believed it had already expired.

---

### Finding Description

`DelegateAction.max_block_height` is documented in two places as an exclusive upper bound:

1. **Field docstring** (`core/primitives/src/action/delegate.rs`, line 60):
   > "The maximal height of the block in the blockchain **below which** the given DelegateAction is valid."

2. **Protocol spec** (`docs/RuntimeSpec/Actions.md`, line 402):
   > "If the current block is **equal or greater than** `max_block_height`" → `DelegateActionExpired`

Both sources say the action is valid only for blocks strictly below `max_block_height`, i.e., it should expire when `block_height >= max_block_height`.

The runtime check in `apply_delegate_action` is:

```rust
// runtime/runtime/src/actions.rs, line 435
if apply_state.block_height > delegate_action.max_block_height() {
    result.result = Err(ActionErrorKind::DelegateActionExpired.into());
    return Ok(());
}
```

This uses `>` (strict), so the action is **accepted** when `block_height == max_block_height`. The unit test `test_delegate_action` (line 1289–1303) explicitly passes `max_block_height` as the current block height and asserts success, confirming the code's behavior. The test `test_delegate_action_max_height` (line 1358–1373) only tests `max_block_height + 1`, leaving the boundary case untested.

The same divergence applies to `DelegateActionV2` (field docstring at line 129 uses identical "below which" wording), since both variants are dispatched through the same `apply_delegate_action` function.

---

### Impact Explanation

A user creates a `SignedDelegateAction` with `max_block_height = H`, reading the spec and expecting the action to be invalid starting at block H. A malicious relayer holds the signed action and submits it at exactly block H. The runtime accepts it (because `H > H` is false), executing arbitrary inner actions (token transfers, function calls, key additions, etc.) on behalf of the user one block after the user believed the authorization had expired.

This breaks the user's time-bounded authorization invariant: the user cannot safely reason about when their signed delegation ceases to be valid, because the spec and the code disagree by one block.

---

### Likelihood Explanation

Any user of meta-transactions (NEP-366) who reads the protocol spec or the field docstring to determine when their `DelegateAction` expires is affected. The attack requires a relayer who:
1. Receives the signed `DelegateAction` before block H.
2. Deliberately withholds submission until block H.
3. Submits at block H.

Relayers are untrusted third parties in the meta-transaction model; the entire point of `max_block_height` is to bound the window of relayer authority. The one-block discrepancy is deterministic and exploitable by any relayer who monitors block heights.

---

### Recommendation

Change the expiry check in `apply_delegate_action` from strict-greater-than to greater-than-or-equal, aligning the runtime with the spec and both field docstrings:

```rust
// runtime/runtime/src/actions.rs, line 435
- if apply_state.block_height > delegate_action.max_block_height() {
+ if apply_state.block_height >= delegate_action.max_block_height() {
    result.result = Err(ActionErrorKind::DelegateActionExpired.into());
    return Ok(());
}
```

Also update the error variant description in `core/primitives/src/errors.rs` (line 806) from "is less than actual block height" to "is less than or equal to actual block height" to match.

Update `test_delegate_action` to use `max_block_height - 1` as the current block height (so it tests a clearly valid case), and add a new test asserting that `block_height == max_block_height` produces `DelegateActionExpired`.

---

### Proof of Concept

**Divergent values:**

| Location | Comparison | Behavior at `block_height == max_block_height` |
|---|---|---|
| `docs/RuntimeSpec/Actions.md:402` | `>=` | Expired |
| `core/primitives/src/action/delegate.rs:60` | `<` (valid "below which") | Expired |
| `runtime/runtime/src/actions.rs:435` | `>` (strict) | **Accepted** |
| `core/primitives/src/errors.rs:806` | `<` ("less than") | Expired |

**Existing test that confirms the off-by-one:**

```rust
// runtime/runtime/src/actions.rs:1289-1303
let apply_state =
    create_apply_state(signed_delegate_action.delegate_action.max_block_height);
// ...
assert!(result.result.is_ok(), ...);  // passes at exactly max_block_height
```

The spec says this should return `DelegateActionExpired`; the code returns `Ok`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** runtime/runtime/src/actions.rs (L434-438)
```rust
    let delegate_action = signed_delegate_action.delegate_action();
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

**File:** core/primitives/src/action/delegate.rs (L59-61)
```rust
    pub nonce: Nonce,
    /// The maximal height of the block in the blockchain below which the given DelegateAction is valid.
    pub max_block_height: BlockHeight,
```

**File:** core/primitives/src/action/delegate.rs (L128-131)
```rust
    pub nonce: TransactionNonce,
    /// The maximal height of the block in the blockchain below which the given DelegateActionV2 is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
```

**File:** docs/RuntimeSpec/Actions.md (L401-407)
```markdown

- If the current block is equal or greater than `max_block_height`

```rust
/// Delegate action has expired
DelegateActionExpired
```
```

**File:** core/primitives/src/errors.rs (L806-807)
```rust
    /// Delegate action has expired. `max_block_height` is less than actual block height.
    DelegateActionExpired = 18,
```
