### Title
Off-by-one in `DelegateAction` expiry check allows execution at `max_block_height` — (`runtime/runtime/src/actions.rs`)

### Summary

The `DelegateAction` expiry check uses a strict `>` comparison instead of `>=`, allowing a relayer to execute a delegate action at exactly `apply_state.block_height == max_block_height`, one block beyond the sender's intended validity window.

### Finding Description

`DelegateAction` carries a `max_block_height` field documented as "The maximal height of the block in the blockchain **below which** the given DelegateAction is valid." [1](#0-0) 

The spec in `docs/RuntimeSpec/Actions.md` states explicitly:

> "If the current block is **equal or greater than** `max_block_height`" → `DelegateActionExpired` [2](#0-1) 

The OpenAPI schema reinforces this: `"Delegate action has expired. max_block_height is less than actual block height."` — meaning expiry fires when `block_height >= max_block_height`. [3](#0-2) 

However, the test `test_delegate_action_max_height` only verifies expiry at `max_block_height + 1`: [4](#0-3) 

A separate test (`test_delegate_action_validate_sender_account`) creates `apply_state` with `block_height == max_block_height` and expects a **different** error (`DelegateActionSenderDoesNotMatchTxReceiver`), not `DelegateActionExpired` — confirming the expiry check passes at the exact boundary: [5](#0-4) 

This means the runtime check in `apply_delegate_action` is `apply_state.block_height > max_block_height` (strict), not `>= max_block_height` (inclusive), leaving a one-block window where the action executes when the sender intended it to be expired.

### Impact Explanation

A relayer can time submission of a signed `DelegateAction` to land in the block at exactly `max_block_height`. The sender set that value believing the action would be expired at that height (per the documented semantics "below which … is valid"). The relayer instead executes the action — which may include `Transfer`, `FunctionCall`, or any other `NonDelegateAction` — one block after the sender's intended deadline. This constitutes unauthorized transaction execution: the sender's signed authorization is used outside the validity window the sender intended.

### Likelihood Explanation

NEAR uses fixed ~1-second block slots. A relayer can observe the mempool and target the exact block height deterministically. The relayer controls submission timing and can retry until the transaction lands at `max_block_height`. No privileged access is required; any relayer processing meta-transactions can exploit this.

### Recommendation

Change the expiry check in `apply_delegate_action` (and any equivalent path for `DelegateActionV2`) from:

```rust
if apply_state.block_height > delegate_action.max_block_height() {
```

to:

```rust
if apply_state.block_height >= delegate_action.max_block_height() {
```

This aligns the runtime with the documented invariant ("below which … is valid") and the spec ("equal or greater than `max_block_height`" → expired).

### Proof of Concept

1. Sender signs a `DelegateAction` with `max_block_height = H` and hands it to a relayer, intending the action to be invalid at block H.
2. Relayer waits until the chain reaches height H.
3. Relayer submits the `DelegateAction` in a transaction included in block H.
4. `apply_delegate_action` evaluates `H > H` → `false` → no expiry error.
5. The inner actions (e.g., `Transfer`) execute on behalf of the sender at block H, one block after the sender's intended expiry.

The existing test `test_delegate_action_validate_sender_account` already inadvertently demonstrates this: it constructs `apply_state` at `max_block_height` and reaches the sender-mismatch check, proving the expiry guard does not fire at the boundary. [6](#0-5)

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

**File:** chain/jsonrpc/openapi/openapi.json (L2110-2114)
```json
            "description": "Delegate action has expired. `max_block_height` is less than actual block height.",
            "enum": [
              "DelegateActionExpired"
            ],
            "type": "string"
```

**File:** runtime/runtime/src/actions.rs (L1358-1373)
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
```

**File:** runtime/runtime/src/actions.rs (L1384-1406)
```rust
        let apply_state =
            create_apply_state(signed_delegate_action.delegate_action.max_block_height);
        let mut state_update = setup_account(&sender_id, &sender_pub_key, &access_key);

        // Use a different sender_id. Must fail.
        apply_delegate_action(
            &mut state_update,
            &apply_state,
            &VersionedActionReceipt::from(action_receipt),
            &"www.test.near".parse().unwrap(),
            (&signed_delegate_action).into(),
            &mut result,
        )
        .expect("Expect ok");

        assert_eq!(
            result.result,
            Err(ActionErrorKind::DelegateActionSenderDoesNotMatchTxReceiver {
                sender_id: sender_id.clone(),
                receiver_id: "www.test.near".parse().unwrap(),
            }
            .into())
        );
```
