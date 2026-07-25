### Title
`DelegateAction` Expiry Off-by-One: Runtime Uses `>` While Spec Mandates `>=` — (`File: runtime/runtime/src/actions.rs`)

---

### Summary

The `DelegateAction` (meta-transaction) runtime check for expiry uses a strict `>` comparison against `max_block_height`, but the protocol specification and the field's own documentation state the action should expire when the current block height is **equal to or greater than** `max_block_height`. This off-by-one discrepancy allows a relayer to execute a signed `DelegateAction` one block beyond the window the signer intended to authorize, enabling unauthorized transaction execution.

---

### Finding Description

The `DelegateAction` struct carries a `max_block_height` field documented as:

> "The maximal height of the block in the blockchain **below which** the given DelegateAction is valid." [1](#0-0) 

The protocol specification in `docs/RuntimeSpec/Actions.md` is equally explicit:

> "If the current block is **equal or greater than** `max_block_height`" → `DelegateActionExpired` [2](#0-1) 

However, the runtime enforcement in `apply_delegate_action` uses a **strict** greater-than:

```rust
if apply_state.block_height > delegate_action.max_block_height() {
    result.result = Err(ActionErrorKind::DelegateActionExpired.into());
    return Ok(());
}
``` [3](#0-2) 

The condition should be `>=` to match the spec. With `>`, when `block_height == max_block_height` the action is **not** rejected — it executes successfully — one block beyond the signer's intended authorization window.

The unit test for this path confirms the discrepancy: it only tests `max_block_height + 1` (strictly greater), never the boundary `max_block_height == block_height`:

```rust
// Setup current block as higher than max_block_height. Must fail.
let apply_state =
    create_apply_state(signed_delegate_action.delegate_action.max_block_height + 1);
``` [4](#0-3) 

A separate test that uses `block_height == max_block_height` expects the action to **succeed** (not expire), which is consistent with the buggy `>` check but inconsistent with the spec: [5](#0-4) 

---

### Impact Explanation

A user signs a `DelegateAction` with `max_block_height = N`, intending the authorization to cover only blocks with height strictly less than N. A relayer who holds this signed action can submit it at block N — which the signer believed was already expired — and the runtime will execute it successfully. This constitutes **unauthorized transaction execution**: the signer's funds, access-key allowance, or contract state can be mutated one block after the signer intended the authorization to lapse.

The `DelegateAction` mechanism is specifically designed for relayer-based meta-transactions where the signer does not control submission timing. The `max_block_height` field is the signer's only tool to bound the authorization window. An off-by-one in its enforcement directly undermines that bound.

---

### Likelihood Explanation

Likelihood is **low**. The attacker window is exactly one block (~1 second on NEAR mainnet). The relayer must already hold a valid signed `DelegateAction` and must time submission precisely to block N. However, the scenario is fully reachable by an unprivileged relayer using only standard JSON-RPC transaction submission — no validator access or privileged role is required.

---

### Recommendation

**Short term:** Change the comparison in `apply_delegate_action` from `>` to `>=`:

```rust
// Before (buggy):
if apply_state.block_height > delegate_action.max_block_height() {

// After (correct, matches spec):
if apply_state.block_height >= delegate_action.max_block_height() {
``` [3](#0-2) 

**Long term:** Add a dedicated boundary test asserting that a `DelegateAction` with `max_block_height = N` is rejected when `block_height == N`, mirroring the existing `max_block_height + 1` test.

---

### Proof of Concept

1. Alice signs a `DelegateAction` with `max_block_height = 100`, intending it to be valid only for blocks 0–99.
2. The relayer holds the signed action and waits until block 100.
3. The relayer submits the action at block 100 via `broadcast_tx_async`.
4. The runtime evaluates `apply_state.block_height (100) > delegate_action.max_block_height() (100)` → `false` → **no expiry error**.
5. The action executes: Alice's funds are transferred or her contract is called at block 100, one block after she intended the authorization to expire.

The existing test infrastructure confirms this: `create_apply_state(max_block_height)` (block height equal to max) does not produce `DelegateActionExpired`. [6](#0-5)

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

**File:** runtime/runtime/src/actions.rs (L1358-1360)
```rust
        // Setup current block as higher than max_block_height. Must fail.
        let apply_state =
            create_apply_state(signed_delegate_action.delegate_action.max_block_height + 1);
```

**File:** runtime/runtime/src/actions.rs (L1376-1386)
```rust
    #[test]
    fn test_delegate_action_validate_sender_account() {
        let mut result = ActionResult::default();
        let (action_receipt, signed_delegate_action) = create_delegate_action_receipt();
        let sender_id = signed_delegate_action.delegate_action.sender_id.clone();
        let sender_pub_key = signed_delegate_action.delegate_action.public_key.clone();
        let access_key = AccessKey { nonce: 19000000, permission: AccessKeyPermission::FullAccess };

        let apply_state =
            create_apply_state(signed_delegate_action.delegate_action.max_block_height);
        let mut state_update = setup_account(&sender_id, &sender_pub_key, &access_key);
```
