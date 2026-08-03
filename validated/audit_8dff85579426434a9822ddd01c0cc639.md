No vulnerability found for this question.

**Analysis:**

`ChunkyDKGStartEvent`'s `MoveStructType` implementation hardcodes `MODULE_NAME = ident_str!("chunky_dkg")` and `STRUCT_NAME = ident_str!("ChunkyDKGStartEvent")` as compile-time constants [1](#0-0) . The `ADDRESS` field defaults to `CORE_CODE_ADDRESS` (0x1) via the `MoveStructType` trait default [2](#0-1) . None of these three fields (`ADDRESS`, `MODULE_NAME`, `STRUCT_NAME`) are derived from runtime/unprivileged input — they are fixed at compile time in the Rust type definition, so no transaction, package, API, or bytecode input can alter what `struct_tag()` produces for this type.

The conversion from a raw `ContractEvent` to `ChunkyDKGStartEvent` explicitly checks the event's on-chain `type_tag` against this fixed `struct_tag()` before deserializing the payload: [3](#0-2) . This `ensure!` guards against binding to the wrong struct/module/address — an event with any other type tag will fail the conversion rather than being silently reinterpreted as a `ChunkyDKGStartEvent`.

Since the reflection constants are immutable Rust-level constants and the runtime check enforces an exact match against them, there is no path by which unprivileged input (transaction, package, API, or bytecode) can cause this authenticated event-decoding logic to bind to the wrong ledger object, version, or proof context.

### Citations

**File:** types/src/dkg/chunky_dkg.rs (L463-466)
```rust
impl MoveStructType for ChunkyDKGStartEvent {
    const MODULE_NAME: &'static IdentStr = ident_str!("chunky_dkg");
    const STRUCT_NAME: &'static IdentStr = ident_str!("ChunkyDKGStartEvent");
}
```

**File:** third_party/move/move-core/types/src/move_resource.rs (L13-17)
```rust
pub trait MoveStructType {
    const ADDRESS: AccountAddress = crate::language_storage::CORE_CODE_ADDRESS;
    const MODULE_NAME: &'static IdentStr;
    const STRUCT_NAME: &'static IdentStr;

```

**File:** types/src/contract_event.rs (L330-341)
```rust
    fn try_from(event: &ContractEvent) -> Result<Self> {
        let ContractEvent::V2(event) = event else {
            bail!("Only ContractEvent::V2 is supported for ChunkyDKGStartEvent");
        };

        ensure!(
            event.type_tag == TypeTag::Struct(Box::new(Self::struct_tag())),
            "Unexpected event type tag for ChunkyDKGStartEvent: {}",
            event.type_tag.to_canonical_string(),
        );
        Ok(bcs::from_bytes(&event.event_data)?)
    }
```
