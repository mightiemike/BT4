### Title
Unbounded recursion via nested `DelegateAction`/`DelegateActionV2` when parsed through the derived `serde::Deserialize` path (JSON), bypassing the recursion guard enforced only for Borsh — potential transaction-triggered stack-overflow DoS - (File: `core/primitives/src/action/delegate.rs`)

### Summary
`NonDelegateAction` is the type used to prevent a `DelegateAction`/`DelegateActionV2` from containing another delegate action, which would otherwise allow unbounded recursive nesting (mirroring the underscore `_.flatten`/`_.isEqual` CWE-674/CWE-770 class: recursion with no depth limit on attacker-influenced nested data). The guard is implemented **only** for `BorshDeserialize`; the `serde::Deserialize` derive on the same type performs no such check.

### Finding Description
`DelegateAction`/`DelegateActionV2` hold their inner actions as `Vec<NonDelegateAction>`: [1](#0-0) [2](#0-1) 

`NonDelegateAction` is explicitly documented as existing "to avoid the recursion when Action/DelegateAction is deserialized," and the comment states the guarantee is relied upon by "borsh de-/serialization": [3](#0-2) 

The actual enforcement is a hand-written `BorshDeserialize` impl that explicitly rejects the delegate variant discriminants: [4](#0-3) 

However, `NonDelegateAction` also derives plain `serde::Deserialize` at its struct definition: [5](#0-4) 

The derived `serde::Deserialize` for a `struct NonDelegateAction(Action)` simply deserializes the inner `Action` field directly with **no check** that it isn't `Action::Delegate`/`Action::DelegateV2`. This means that whenever a `DelegateAction`/`DelegateActionV2`/`SignedDelegateAction` is deserialized from JSON (rather than Borsh), an attacker can nest delegate actions inside delegate actions to unbounded depth, exactly matching the `_.flatten`/`_.isEqual` bug class: a recursive data structure with no depth limit, later processed by recursive code (hashing via `get_nep461_hash` → `borsh::to_vec`, derived `PartialEq`, `Drop` of nested `Box<SignedDelegateAction>` chains, etc.), leading to a stack overflow.

The same double-standard pattern (guard only enforced by `TryFrom`, not by the derived `serde::Deserialize`) also appears in the Rosetta RPC model layer, where `NonDelegateActionOperation` wraps `Operation` and again only gates construction via `TryFrom`, while deriving plain `serde::Deserialize`: [6](#0-5) 

Rosetta RPC is one of the two externally-reachable RPC servers in this codebase (Rosetta Construction API), documented alongside the main JSON-RPC server: [7](#0-6) 

By contrast, the primary `broadcast_tx_*` JSON-RPC path takes a Borsh-encoded, base64 `SignedTransaction` (`signed_tx_base64`), which does route through the guarded `BorshDeserialize` impl and is therefore not affected: [8](#0-7) 

### Impact Explanation
If any externally reachable code path deserializes `DelegateAction`/`DelegateActionV2`/`Operation` structures from JSON (as opposed to Borsh) — which is the case for the Rosetta Construction API surface that models these types with `serde::Deserialize`/`schemars`/`ToSchema` — an attacker can submit a deeply nested delegate-action payload. Because the anti-nesting invariant is not enforced on that code path, the resulting in-memory structure can recurse to attacker-chosen depth. Subsequent recursive operations on it (hash computation for signature verification, equality comparisons, `Drop`) can then overflow the stack, crashing the node process handling the request. A transaction-triggered/RPC-triggered process crash is a valid high-severity DoS impact under the given rules.

### Likelihood Explanation
Likelihood is high given a caller can reach any JSON deserialization entry point for these types without any privilege beyond being a network client of the RPC/Rosetta endpoint (no signature verification is needed to trigger the recursive deserialize; verification and validation only happen after the structure already exists in memory, if at all before the overflow occurs). The condition depends on there existing a live code path that deserializes `Action`/`DelegateAction`/`Operation` via `serde_json` from an unauthenticated caller — this is architecturally the Rosetta Construction API, which is designed to accept externally supplied unsigned/partially-signed operation graphs.

### Recommendation
Implement a custom `serde::Deserialize` for `NonDelegateAction` (and any other type relying on the "cannot contain a nested delegate action" invariant, e.g. `NonDelegateActionOperation`) that mirrors the existing `BorshDeserialize` guard — i.e., deserialize into `Action`/`Operation` and then apply the existing `TryFrom` rejection logic (`IsDelegateAction`/`IsDelegateOperation`) inside `deserialize`, returning a `serde::de::Error` instead of successfully constructing an invalid nested value. Additionally, enforce a global recursion/depth limit for any JSON-based transaction/action-construction API, independent of the type-level guard, as a defense-in-depth measure against this class of bug.

### Proof of Concept
Conceptually (mirrors the `_.isEqual`/`_.flatten` PoC pattern): construct a JSON document representing a `DelegateAction` whose `actions` array contains a `NonDelegateAction` entry that is itself an `Action::Delegate(SignedDelegateAction)` wrapping another `DelegateAction`, and repeat this nesting thousands of levels deep. Submit this JSON body to any endpoint that performs `serde_json::from_slice`/`from_value` into `DelegateAction`/`NonDelegateAction`/`Operation` (e.g., the Rosetta Construction API payload endpoints). Because the derived `Deserialize` impl for `NonDelegateAction` does not call the `TryFrom<Action>` guard that rejects delegate variants, the nested structure deserializes successfully; a subsequent recursive traversal (hash computation, equality check, or `Drop`) then overflows the stack and crashes the serving process.

*Note on confidence: I was able to fully confirm the missing-guard root cause in `core/primitives/src/action/delegate.rs` and the parallel pattern in `chain/rosetta-rpc/src/models.rs`, but I was not able to trace, within the remaining tool budget, the exact Axum/Rosetta handler function that performs `serde_json` deserialization of these specific nested types from an unauthenticated request body. That final reachability link should be verified directly in the repository (e.g., `chain/rosetta-rpc/src/adapters/mod.rs` and the `/construction/*` handlers) before treating this as fully confirmed end-to-end.*

### Citations

**File:** core/primitives/src/action/delegate.rs (L34-45)
```rust
#[derive(
    BorshSerialize,
    BorshDeserialize,
    Serialize,
    Deserialize,
    PartialEq,
    Eq,
    Clone,
    Debug,
    ProtocolSchema,
)]
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
```

**File:** core/primitives/src/action/delegate.rs (L51-55)
```rust
    /// List of actions to be executed.
    ///
    /// With the meta transactions MVP defined in NEP-366, nested
    /// DelegateActions are not allowed. A separate type is used to enforce it.
    pub actions: Vec<NonDelegateAction>,
```

**File:** core/primitives/src/action/delegate.rs (L124-125)
```rust
    /// List of actions to be executed.
    pub actions: Vec<NonDelegateAction>,
```

**File:** core/primitives/src/action/delegate.rs (L360-371)
```rust
/// This is Action which mustn't contain DelegateAction.
///
/// This struct is needed to avoid the recursion when Action/DelegateAction is deserialized.
///
/// Important: Don't make the inner Action public, this must only be constructed
/// through the correct interface that ensures the inner Action is actually not
/// a delegate action. That would break an assumption of this type, which we use
/// in several places. For example, borsh de-/serialization relies on it. If the
/// invariant is broken, we may end up with a `Transaction` or `Receipt` that we
/// can serialize but deserializing it back causes a parsing error.
#[derive(Serialize, BorshSerialize, Deserialize, PartialEq, Eq, Clone, Debug, ProtocolSchema)]
pub struct NonDelegateAction(Action);
```

**File:** core/primitives/src/action/delegate.rs (L433-443)
```rust
    impl borsh::de::BorshDeserialize for NonDelegateAction {
        fn deserialize_reader<R: Read>(rd: &mut R) -> ::core::result::Result<Self, Error> {
            match u8::deserialize_reader(rd)? {
                n if DELEGATE_VARIANT_NUMBERS.contains(&n) => Err(Error::new(
                    ErrorKind::InvalidInput,
                    "DelegateAction mustn't contain a nested one",
                )),
                n => borsh::de::EnumExt::deserialize_variant(rd, n).map(Self),
            }
        }
    }
```

**File:** chain/rosetta-rpc/src/models.rs (L907-939)
```rust
/// This is Operation which mustn't contain DelegateActionOperation.
///
/// This struct is needed to avoid the recursion when Action/DelegateAction is deserialized.
///
/// Important: Don't make the inner Action public, this must only be constructed
/// through the correct interface that ensures the inner Action is actually not
/// a delegate action. That would break an assumption of this type, which we use
/// in several places. For example, borsh de-/serialization relies on it. If the
/// invariant is broken, we may end up with a `Transaction` or `Receipt` that we
/// can serialize but deserializing it back causes a parsing error.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize, ToSchema)]
pub(crate) struct NonDelegateActionOperation(crate::models::Operation);

impl From<NonDelegateActionOperation> for crate::models::Operation {
    fn from(action: NonDelegateActionOperation) -> Self {
        action.0
    }
}

#[derive(serde::Serialize, serde::Deserialize, PartialEq, Clone, Debug, thiserror::Error)]
#[error("Delegate operation cannot contain a delegate operation")]
pub struct IsDelegateOperation;

impl TryFrom<crate::models::Operation> for NonDelegateActionOperation {
    type Error = IsDelegateOperation;

    fn try_from(operation: crate::models::Operation) -> Result<Self, IsDelegateOperation> {
        if matches!(operation.type_, crate::models::OperationType::DelegateAction) {
            Err(IsDelegateOperation)
        } else {
            Ok(Self(operation))
        }
    }
```

**File:** chain/jsonrpc/RPC_ARCHITECTURE.md (L61-65)
```markdown
1. **JSON-RPC Server** (`chain/jsonrpc/`) - Primary node interface. Port 3030 by default. Includes light client endpoints (`next_light_client_block`, `light_client_proof`, etc.) which provide Merkle proofs for trustless verification.
2. **Rosetta RPC Server** (`chain/rosetta-rpc/`) - Rosetta API compatible server for exchanges. Port 3040 by default.

Both communicate with internal actors (`ClientActor`, `ViewClientActor`, `RpcHandlerActor`) via async message passing.

```

**File:** chain/jsonrpc-primitives/src/types/transactions.rs (L1-12)
```rust
use near_primitives::hash::CryptoHash;
use near_primitives::types::{AccountId, ShardId};
use serde_json::Value;

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
pub struct RpcSendTransactionRequest {
    #[serde(rename = "signed_tx_base64")]
    pub signed_transaction: near_primitives::transaction::SignedTransaction,
    #[serde(default)]
    pub wait_until: near_primitives::views::TxExecutionStatus,
}
```
