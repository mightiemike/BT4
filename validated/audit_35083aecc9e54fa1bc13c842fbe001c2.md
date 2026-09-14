## Title
Unbounded recursive JSON deserialization of nested `DelegateAction`/`NonDelegateAction` bypasses the Borsh anti-nesting guard, allowing stack-exhaustion crash of a node process from untrusted RPC input - (`core/primitives/src/action/delegate.rs`, `chain/rosetta-rpc/src/models.rs`)

### Summary
The Apache Traffic Server bug class is "recursive parser has no depth limit, so attacker-controlled deeply-nested input exhausts the stack and crashes the process." nearcore has the *same* structural weakness in the way it guards against nested `DelegateAction`s: the guard is implemented **only** for the Borsh codec, but the JSON (`serde`) codec for the identical types is derived automatically and performs no such check, and no depth limit is enforced on `serde_json` recursive descent anywhere the type is exposed to network input (e.g. Rosetta RPC's construction API).

### Finding Description
`DelegateAction` (a meta-transaction) must not contain another `DelegateAction`, to prevent infinite/unbounded recursion. This is enforced for Borsh via a hand-written `BorshDeserialize` impl that inspects the discriminant byte before recursing: [1](#0-0) 

However, the very same struct also derives `serde::Deserialize` directly at the type definition with **no equivalent check**: [2](#0-1) 

Because the tuple field is only "protected" by construction-time `TryFrom`, but the `#[derive(..., Deserialize, ...)]` macro generates code that deserializes the inner `Action` directly (bypassing `TryFrom`), any JSON entry point that ends up calling `serde_json`'s recursive-descent deserializer on `Action`/`NonDelegateAction`/`DelegateAction` has no protection against attacker-supplied, arbitrarily deep nesting of `Delegate`/`DelegateV2` actions.

The Rosetta RPC surface exhibits the exact analogous pattern for its own `Operation`/`DelegateAction` JSON model, with the same doc comment acknowledging the recursion risk but again only enforcing it via a post-hoc `TryFrom`, not during `Deserialize`: [3](#0-2) 

`serde_json`'s default `Value`/enum deserialization is recursive (one Rust stack frame per JSON nesting level for arrays/objects/enum variants), and unlike the WASM contract runtime, which is protected by the explicit, gas-metered `max_stack_height` instrumentation described in `docs/RuntimeSpec/Preparation.md`, the **host-side Rust deserialization code that runs before any contract or gas accounting even begins** has no such stack-depth guard. This mirrors the ATS `uri_signing`/`url_sig` bug class precisely: an unbounded, attacker-controlled recursive descent runs in privileged host code, prior to any resource metering.

### Impact Explanation
A JSON payload representing a deeply nested chain of `DelegateAction`/`Operation` objects (e.g., submitted to the Rosetta `/construction/*` endpoints, which build `Action`/`SignedTransaction` structures from client-supplied JSON `Operation`s) can drive `serde_json`'s recursive descent parser to exhaust the native call stack before any of nearcore's transaction/action validation, gas metering, or the `NonDelegateAction::TryFrom` delegate check ever executes. In Rust, stack overflow from unbounded recursion is not a catchable panic — it aborts the process (SIGSEGV/abort), crashing the entire node process that hosts the Rosetta/JSON-RPC actor system, not just the handling request. Because nearcore runs its actors (`ClientActor`, `ViewClientActor`, chain/RPC actors) in the same process, this is a full node crash reachable by a single unauthenticated network request, i.e., a remotely-triggerable denial of service against any node exposing the affected JSON endpoint.

### Likelihood Explanation
Exploitation requires only an HTTP/JSON request to a node exposing the Rosetta RPC construction API (or any other JSON entry point that deserializes `Action`/`DelegateAction`/`Operation` types) with a deeply nested, crafted body — no signature, account, gas, or protocol-level validity is needed to reach the vulnerable deserialization code, since that runs *before* semantic transaction validation. This makes it trivially reachable by any unprivileged network caller.

### Recommendation
- Implement a custom `serde::Deserialize` for `NonDelegateAction` (and the Rosetta `NonDelegateActionOperation`) that mirrors the `BorshDeserialize` guard: reject variants matching `DELEGATE_VARIANT_NUMBERS`/`DELEGATE_VARIANT_NAMES` during deserialization rather than only after the fact via `TryFrom`.
- Alternatively/additionally, bound recursion depth explicitly, e.g. using a depth-limited deserializer (`serde_stacker`, or a manual iterative pass) for any JSON structure that can nest `Action`/`Operation`/`DelegateAction` values, so that stack usage is capped regardless of derive-macro behavior.
- Audit all other types where a hand-written `BorshDeserialize`/`BorshSerialize` guard exists specifically to prevent malicious recursive structures, and verify the `serde` (JSON) derive counterpart enforces the same invariant.

### Proof of Concept
Conceptually (cannot execute in this environment):
1. Construct a JSON body for the Rosetta `/construction/payloads` (or `/construction/parse`) endpoint containing an `Operation` of type `DELEGATE_ACTION` whose metadata embeds another `DELEGATE_ACTION` operation, nested to a depth of e.g. 100,000+ levels (trivial to generate programmatically, purely as a JSON string of repeated nested objects).
2. Submit this JSON body to the node's Rosetta RPC endpoint.
3. `serde_json`'s deserializer recurses once per nesting level while parsing into `models::Operation` (and transitively `NonDelegateActionOperation`), before the `TryFrom<Operation> for NonDelegateActionOperation` delegate-rejection check is ever invoked.
4. The native thread stack is exhausted, the Rust runtime aborts the process (uncatchable), and the entire nearcore node process crashes. [2](#0-1) [4](#0-3)

### Citations

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

**File:** core/primitives/src/action/delegate.rs (L409-443)
```rust
/// A small private module to protect the private fields inside `NonDelegateAction`.
mod private_non_delegate_action {
    use super::*;

    impl From<NonDelegateAction> for Action {
        fn from(action: NonDelegateAction) -> Self {
            action.0
        }
    }

    #[derive(Debug, thiserror::Error)]
    #[error(
        "attempted to construct NonDelegateAction from a delegate action (Delegate or DelegateV2)"
    )]
    pub struct IsDelegateAction;

    impl TryFrom<Action> for NonDelegateAction {
        type Error = IsDelegateAction;

        fn try_from(action: Action) -> Result<Self, IsDelegateAction> {
            if action.is_delegate() { Err(IsDelegateAction) } else { Ok(Self(action)) }
        }
    }

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
