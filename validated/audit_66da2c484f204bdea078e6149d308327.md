### Title
Argument Injection via Unescaped Comma in Wallet Contract `method_names` Join Allows Access-Key Permission Scope Expansion - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The NEAR Wallet Contract (NEP-518, used by ETH-implicit accounts) decodes `AddKey` actions from ABI-encoded Ethereum transaction calldata as a structured array of method-name strings, then serializes that array into a single comma-joined string before calling the promise host function. On the runtime/VM side, that string is re-split on `,` to reconstruct the method list. Because individual ABI-decoded method-name strings are never checked for embedded `,` characters, a single logical array element containing a comma is silently expanded into multiple method names once it reaches the runtime, breaking the 1:1 correspondence between the ABI-encoded list and the on-chain `FunctionCallPermission.method_names` that gets persisted.

### Finding Description
`action_to_promise` in the wallet contract converts an `AddKey`/`FunctionCall` permission into a promise call: [1](#0-0) 

Note `access.method_names.join(",")` at line 493 — the `Vec<String>` decoded from `ADD_KEY_SIGNATURE`'s `ethabi::Token::Array(String)` (see `runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs`) is flattened with a comma separator with no escaping and no rejection of method names that already contain `,`.

This joined string is passed to the SDK's `add_access_key_allowance_with_nonce`, which lowers to the host function `promise_batch_action_add_key_with_function_call`. On the runtime side, the raw method-names buffer is split back into a vector using a naive comma-delimited split: [2](#0-1) 

`split_method_names` has no way to distinguish "one method name containing a literal comma" from "two method names separated by a comma" — it always treats `,` as a separator. Protocol-level validation of method names (`validate_access_key_permission` in `runtime/runtime/src/action_validation.rs`) only checks length limits, not character content, so a comma is a legal byte in a method name at every earlier stage (ABI decode, `near_action::FunctionCallPermission`, and the eventual `AccessKey`/`FunctionCallPermission` written to state via `runtime/runtime/src/receipt_manager.rs::append_action_add_key_with_function_call`).

The net effect: if the Ethereum-signed calldata specifies a single ABI array element such as `"swap,admin_withdraw"` (a value the signer/relayer/dApp UI may treat and display as one opaque method identifier), the wallet contract's join/split round-trip through the host function causes the access key that is actually written on-chain to grant `FunctionCallPermission.method_names = ["swap", "admin_withdraw"]` — i.e., two distinct permitted methods instead of the one implied by the ABI-encoded array cardinality. This is the same bug class as CVE-2025-31499: data that is supposed to be treated as an opaque value is silently re-interpreted as a delimiter/control sequence by a downstream component, expanding the effective permission/argument set beyond what the upstream structured encoding specified.

### Impact Explanation
This breaks the integrity guarantee that a `FunctionCallPermission` access key's allowed methods correspond exactly to what was authorized in the structured (ABI array) representation of the action. Any tooling, relayer, or wallet UI that displays/validates method names as ABI array elements (rather than as the flattened runtime string) can be misled into believing a key is scoped to a single, narrower method, while the actual on-chain `AccessKey` permits an additional, attacker-chosen method name. This is an access-control/authorization-scope integrity defect: it allows an unauthorized method to be silently added to the reachable set of a function-call-restricted key for an ETH-implicit (wallet-contract) account, which can subsequently be leveraged (depending on which contract/method the smuggled name matches) for unauthorized value movement or unintended privileged calls through that key.

### Likelihood Explanation
Reachable end-to-end from a single, ordinary `rlp_execute` transaction on an ETH-implicit account — no validator/relayer collusion, no network-layer assumptions, and no special privileges are required beyond normal use of the Wallet Contract's `AddKey` action. The only precondition is that a method name string containing a `,` character reaches the ABI-encoded `method_names` array (e.g., via a dApp/SDK that builds this array from partially attacker-influenced or unsanitized strings, or a user who doesn't realize `,` is treated specially downstream). Since neither the wallet contract's ABI decode/encode path nor the protocol's `validate_access_key_permission` reject commas in method names, nothing currently prevents this expansion.

### Recommendation
- Reject (or percent/URL-encode) `,` characters in individual `method_names` entries before joining them in `action_to_promise` (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`), returning a `UserError` if any entry contains the separator.
- Alternatively, change the wire format between the wallet contract and `promise_batch_action_add_key_with_function_call` to use a length-prefixed/escaped encoding instead of naive comma-joining, so `split_method_names` in `runtime/near-vm-runner/src/logic/utils.rs` cannot conflate "one name with an embedded comma" with "two names."
- Add a check in `validate_access_key_permission` (`runtime/runtime/src/action_validation.rs`) or in `split_method_names` itself to reject method names containing `,` (or any character it cannot round-trip), closing the same class of confusion at the protocol boundary as well.

### Proof of Concept
1. Craft (and sign with the target's Secp256k1 key) an Ethereum-style transaction whose calldata matches `ADD_KEY_SIGNATURE`/`ADD_KEY_SELECTOR`, with `method_names = ["swap,admin_withdraw"]` (a single array element containing an embedded comma).
2. Submit it via a relayer to `rlp_execute(target, tx_bytes_b64)` on the ETH-implicit account's Wallet Contract, per the flow in `runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs::parse_rlp_tx_to_action` → `parse_tx_data` (ADD_KEY_SELECTOR branch) → `types.rs::Action::try_into_near_action`.
3. The resulting `near_action::Action::AddKey` carries `FunctionCallPermission.method_names == ["swap,admin_withdraw"]` (one element).
4. `action_to_promise` (`lib.rs:475-501`) calls `access.method_names.join(",")`, producing the byte string `b"swap,admin_withdraw"`, which is passed to `promise_batch_action_add_key_with_function_call`.
5. The runtime's `split_method_names` (`runtime/near-vm-runner/src/logic/utils.rs:11-20`) splits this into `[b"swap", b"admin_withdraw"]`, and `append_action_add_key_with_function_call` (`runtime/runtime/src/receipt_manager.rs:601-631`) persists `FunctionCallPermission.method_names = ["swap", "admin_withdraw"]` on-chain — an access key permitting two methods where only one opaque string was ABI-encoded and (by the signer's expectation) authorized.

Note: I was unable to fully trace whether any existing test in `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/` already covers commas inside a single `method_names` element; only the general-purpose `method_names` tests found (`relayer.rs`, `user_error.rs`) were indexed, so this specific edge case's test coverage is unconfirmed and would need direct verification in a full checkout.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L484-496)
```rust
        near_action::Action::AddKey(action) => match action.access_key.permission {
            near_action::AccessKeyPermission::FullAccess => {
                Err(Error::User(UserError::UnsupportedAction(UnsupportedAction::AddFullAccessKey)))
            }
            near_action::AccessKeyPermission::FunctionCall(access) => Ok(Promise::new(target)
                .add_access_key_allowance_with_nonce(
                    action.public_key,
                    access.allowance.and_then(Allowance::limited).unwrap_or(Allowance::Unlimited),
                    access.receiver_id,
                    access.method_names.join(","),
                    action.access_key.nonce,
                )),
        },
```

**File:** runtime/near-vm-runner/src/logic/utils.rs (L8-20)
```rust
/// Uses `,` separator to split `method_names` into a vector of method names.
/// Returns an empty vec if the empty slice is given.
/// Throws `HostError::EmptyMethodName` in case there is an empty method name inside.
pub(crate) fn split_method_names(method_names: &[u8]) -> Result<Vec<Vec<u8>>, HostError> {
    if method_names.is_empty() {
        Ok(vec![])
    } else {
        method_names
            .split(|c| *c == b',')
            .map(|v| if v.is_empty() { Err(HostError::EmptyMethodName) } else { Ok(v.to_vec()) })
            .collect()
    }
}
```
