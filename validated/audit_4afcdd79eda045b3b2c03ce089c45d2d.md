No vulnerability found for this question.

The file `types/src/account_config/events/burn_event.rs` only defines a static `MoveStructType` implementation (`MODULE_NAME = "collection"`, `STRUCT_NAME = "BurnEvent"`) and a `TOKEN_OBJECTS_ADDRESS`-scoped `StructTag` constant used to identify/parse `BurnEvent` Move events during deserialization [1](#0-0) . These are compile-time constants with no dynamic input path — they are not derived from, or influenced by, any unprivileged transaction, package, API, view, bytecode, or proof input, and they play no role in writing, committing, or verifying ledger state, proofs, accumulators, or authenticated responses. There is no mechanism here by which unprivileged input could corrupt committed state, misbind a proof, or create divergence, so this does not meet the required state-integrity impact criteria.

### Citations

**File:** types/src/account_config/events/burn_event.rs (L39-53)
```rust
impl MoveStructType for BurnEvent {
    const MODULE_NAME: &'static IdentStr = ident_str!("collection");
    const STRUCT_NAME: &'static IdentStr = ident_str!("BurnEvent");
}

impl MoveEventV1Type for BurnEvent {}

pub static BURN_EVENT_TYPE: Lazy<TypeTag> = Lazy::new(|| {
    TypeTag::Struct(Box::new(StructTag {
        address: TOKEN_OBJECTS_ADDRESS,
        module: ident_str!("collection").to_owned(),
        name: ident_str!("BurnEvent").to_owned(),
        type_args: vec![],
    }))
});
```
