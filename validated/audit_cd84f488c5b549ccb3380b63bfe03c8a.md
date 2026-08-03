### Title
`TypeInfo.struct_name` byte-divergence between legacy VM and mono-move VM for generic structs instantiated with function-type (closure) type arguments - ([File: third_party/move/mono-move/natives/src/type_info.rs])

### Summary
The legacy VM's `native_type_of` (aptos-move/framework/natives/src/type_info.rs) and the new mono-move VM's `native_type_of` (third_party/move/mono-move/natives/src/type_info.rs) do **not** produce byte-identical `struct_name` strings for a generic type `T` whose type arguments include a function type (closure). Both natives are meant to return the exact same `aptos_std::type_info::TypeInfo{account_address, module_name, struct_name}` value for any `T`, and the mono-move implementation itself carries an explicit unresolved TODO admitting this has not been verified.

### Finding Description
The legacy native builds `struct_name` from `StructTag::type_args`, formatting each argument with `TypeTag::to_canonical_string()`: [1](#0-0) 

For a function-type argument, `TypeTag::Function`'s canonical string wraps the *args* and *results* lists with `|`...`|(`...`)` and appends the ability set as a suffix: [2](#0-1) 
e.g. an empty function type renders as `"||()"`, and one with abilities as `"||() has copy"` (see the test vectors), always keeping the parentheses around the result list and appending `" has <abilities>"`.

The mono-move native instead formats the type argument via `type_to_string`, which for `Type::Function` renders only:
```rust
write!(f, "|")?;
display_type_list(f, *args)?;
write!(f, "|")?;
display_type_list(f, *results)?;
``` [3](#0-2) 

This omits the parentheses around the result list and completely omits the ability-set suffix that the legacy `FunctionTag::to_canonical_string()` always appends. For example, an empty function type renders as `"||"` under mono-move versus `"||()"` under the legacy VM, and any non-empty abilities (`copy`/`drop`/`store`/`key`) present on the function type are dropped entirely from the mono-move output.

Because struct type arguments in Move (post bytecode-v8) can themselves be function types — confirmed by the legacy VM's own canonical-string test fixtures such as `"0x1::a::A<||||() has copy|()|(||() has copy + drop + store + key)>"` — a struct `T = 0x1::a::A<SomeFunctionType>` will produce a *different* `struct_name` byte string depending on which native executes `type_of<T>()`.

The mono-move source explicitly flags this unresolved risk: [4](#0-3) 
and the sibling `native_type_name` has the same caveat: [5](#0-4) 

### Impact Explanation
`type_of<T>()` returns a `TypeInfo` Move value that user code can freely store in a resource (`move_to`), pack into events, or use as a map/table key. If that value is committed to global storage, the resulting write set entry (a BCS-serialized `vector<u8>` for `struct_name`) will differ between the two VM implementations for the exact same transaction at the exact same ledger state whenever the invoked generic type carries a function-type argument. This is precisely the "corrupted value: write set entry containing serialized TypeInfo resource" scenario described in the exploit question — it breaks execution determinism across VM implementations for identical inputs at the same protocol version, which is the invariant required for consensus-independent state-root verifiability if both VMs were ever run in production side-by-side (e.g., during a migration window or by heterogeneous validator implementations).

### Likelihood Explanation
Reachability requires two things: (1) function types (closures) usable as generic struct type arguments — supported by the `TypeTag::Function` bytecode-v8 representation already present in `move-core-types`; and (2) the mono-move VM actually executing user transactions in a production/consensus-relevant context alongside the legacy VM. Based on the code inspected, mono-move is explicitly under active development — the natives file itself contains unresolved `TODO(correctness)` comments stating the output has not yet been checked against the legacy VM. I could not confirm from the indexed files whether mono-move is currently enabled for mainnet transaction execution or is still experimental/off-path. If it is not yet wired into consensus execution, this is a latent correctness bug rather than an exploitable mainnet divergence today; if/when mono-move is enabled for execution, this becomes a straightforward, deterministic, unprivileged trigger (any account can publish a module defining such a generic struct and call `type_of` on it).

### Recommendation
Fix `display_type`'s `Type::Function` branch in `third_party/move/mono-move/core/src/types.rs` to exactly mirror `FunctionTag::to_canonical_string()`: wrap the result list in parentheses and append the ability-set display suffix, matching the legacy `|{args}|({results}){abilities}` format byte-for-byte. Add a differential test (as hinted by the existing `third_party/move/mono-move/testsuite/tests/test_cases/differential/natives/type_of.move`) that specifically covers structs instantiated with function-type arguments carrying various ability sets, to close out the `TODO(correctness)` markers before mono-move is enabled for any production execution path.

### Proof of Concept
1. Publish a module defining `struct Wrapper<phantom T> has drop {}` and a function whose signature uses a closure type parameter, e.g. `fun mk(): Wrapper<||u64 has copy>`.
2. Call `type_info::type_of<Wrapper<||u64 has copy>>()` once under the legacy VM (`aptos-move/framework/natives/src/type_info.rs`) and once under the mono-move VM (`third_party/move/mono-move/natives/src/type_info.rs`) at the same ledger state.
3. Legacy VM yields `struct_name = b"Wrapper<||u64 has copy>"`-style output following `FunctionTag::to_canonical_string()` (parens + `has copy` suffix); mono-move yields `struct_name` missing the parens around the result and missing `has copy` entirely, per `display_type`'s `Type::Function` arm.
4. Store the resulting `TypeInfo` in a resource; the committed write set bytes differ between the two engines for byte-identical input, demonstrating the divergence.

### Citations

**File:** aptos-move/framework/natives/src/type_info.rs (L20-29)
```rust
fn type_of_internal(struct_tag: &StructTag) -> Result<SmallVec<[Value; 1]>, std::fmt::Error> {
    let mut name = struct_tag.name.to_string();
    if let Some(first_ty) = struct_tag.type_args.first() {
        write!(name, "<")?;
        write!(name, "{}", first_ty.to_canonical_string())?;
        for ty in struct_tag.type_args.iter().skip(1) {
            write!(name, ", {}", ty.to_canonical_string())?;
        }
        write!(name, ">")?;
    }
```

**File:** third_party/move/move-core/types/src/language_storage.rs (L376-401)
```rust
impl FunctionTag {
    /// Returns a canonical string representation of the function tag.
    ///
    /// INVARIANT: If two function tags are different, they must have different canonical strings.
    pub fn to_canonical_string(&self) -> String {
        let fmt_list = |l: &[FunctionParamOrReturnTag]| -> String {
            l.iter()
                .map(|t| t.to_canonical_string())
                .collect::<Vec<_>>()
                .join(", ")
        };
        // Note that we put returns in parentheses. This ensures that when functions used as type
        // arguments, there is no ambiguity in presence of multiple returns, e.g.,
        //
        //    0x1::a::A<||||>
        //
        // is ambiguous: is it a function that has zero arguments and returns a function ||, or is
        // it a function that takes || argument and returns nothing? In order to disambiguate, we
        // always add parentheses for returns.
        format!(
            "|{}|({}){}",
            fmt_list(&self.args),
            fmt_list(&self.results),
            self.abilities.display_postfix()
        )
    }
```

**File:** third_party/move/mono-move/core/src/types.rs (L443-449)
```rust
        Type::Function { args, results, .. } => {
            write!(f, "|")?;
            display_type_list(f, *args)?;
            write!(f, "|")?;
            display_type_list(f, *results)?;
            Ok(())
        },
```

**File:** third_party/move/mono-move/natives/src/type_info.rs (L20-21)
```rust
// TODO(correctness, metering): `type_to_string` is a placeholder — check it against the canonical
// string format the legacy VM uses.
```

**File:** third_party/move/mono-move/natives/src/type_info.rs (L84-87)
```rust
// TODO(completeness): with monomorphization `T` is fully known at specialization time, so the
// specializer could synthesize this `TypeInfo` directly rather than via a native.
//
// TODO(correctness): double check that the result matches the legacy VM's completely.
```
