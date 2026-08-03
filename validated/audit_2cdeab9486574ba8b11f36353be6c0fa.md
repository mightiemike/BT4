# StorageSlot<T> Capability Forgery via BCS Deserialization

## Title
Unauthorized Resource Mutation via BCS-Forged `StorageSlot<T>` Capability - (File: aptos-move/framework/aptos-framework/sources/datastructures/storage_slot.move)

### Summary
`StorageSlot<T>` is designed as an unforgeable capability: the only way to legitimately obtain one is via `storage_slot::new`, which creates a fresh unique on-chain object and stores the value in a `StorageSlotResource<T>` at that address [1](#0-0) . However, because `StorageSlot<T>` is a plain struct containing only an `address` field with the `store` ability (no `key`, no custom invariant enforcement outside the defining module) [2](#0-1) , its byte layout is trivially reproducible. Move's generic `bcs::from_bytes<T>` native constructs values purely from byte layout and does **not** go through a type's defining module or respect module-private struct-literal construction rules. This means any unprivileged module that knows (a) the concrete type `T` used elsewhere in the system and (b) the target on-chain address of an existing `StorageSlotResource<T>` (addresses are public — visible in transaction outputs, events, or state reads) can synthesize a `StorageSlot<T>{ addr }` value out of thin air via `bcs::from_bytes<StorageSlot<T>>(bcs::to_bytes(&victim_addr))`, without ever calling `new`.

### Finding Description
`borrow_mut` and `borrow` do not perform any ownership check beyond trusting the `addr` field of `self`: [3](#0-2) 

The underlying natives resolve global storage purely by the `addr` field extracted from the struct and the type parameters supplied at the call site — they perform no check that the caller legitimately owns/derived this `StorageSlot<T>`: [4](#0-3) 

Because Move's `bcs::from_bytes<T>` is a fully generic native that reconstructs a value purely from its serialized byte layout, it bypasses the language-level guarantee that only the declaring module (`aptos_framework::storage_slot`) can pack/unpack a `StorageSlot<T>` literal. An attacker module can therefore synthesize `StorageSlot<T>{ addr: victim_addr }` for any address it can observe (from an event, a state read, or a transaction output) and any type `T` it can name, provided a `StorageSlotResource<T>` already exists at that address. Calling `.borrow_mut()` on the forged value then lets the attacker mutate the victim's stored value directly, even though the attacker never legitimately received a `StorageSlot<T>` capability for that resource.

### Impact Explanation
This breaks the entire capability/authorization model that `StorageSlot<T>` is meant to enforce: possession of the (supposedly unforgeable) `StorageSlot<T>` handle is treated by every caller of `borrow`/`borrow_mut` as proof of exclusive access rights to the underlying value. If that handle can be forged from public information, unprivileged transactions can produce write sets that mutate state at object addresses the caller was never authorized to touch — a form of state corruption originating purely from unprivileged transaction/module input, matching the "authenticated API/proof binding" concern in the review scope (the write set no longer corresponds to state the submitting account had a legitimate capability over, even though the VM's execution is itself deterministic).

### Likelihood Explanation
The precondition is fully within reach of any unprivileged module: it must know a `StorageSlotResource<T>` address (which is not a secret — such addresses are visible via events, resource fields, indexer/API responses, or global state reads) and the concrete type `T`, which is typically a well-known public type in application code that embeds `StorageSlot<T>` in its own resources. Any Aptos module or contract that stores a `StorageSlot<T>` in an accessible location (e.g., inside a `key`-able resource whose address or fields are queryable) is exposed. Feature flag `is_storage_slot_natives_enabled` must be on, which is expected in normal operation once this feature ships.

### Recommendation
Do not rely on struct-literal privacy of `StorageSlot<T>` for capability safety, since it is undermined by generic `bcs::from_bytes`. Mitigations include: (1) giving `StorageSlotResource<T>` (or a companion marker resource) a random/secret component (e.g., a stored owner check or a witness value verified on borrow) rather than trusting the address alone; (2) storing an authenticated tag inside `StorageSlotResource<T>` (e.g., the creator's address or a randomly-generated nonce) and having `borrow`/`borrow_mut` verify it matches something derivable only by the legitimate holder; or (3) blocking generic `bcs::from_bytes` instantiation for `storage_slot::StorageSlot<T>` specifically (if the Move VM/BCS layer supports type-based denylisting), so forged instances cannot be constructed outside the defining module.

### Proof of Concept
```move
// Attacker module, given a public type `T = SomeType` used elsewhere,
// and `victim_addr` obtained from a public event/state read that reveals
// the address of a StorageSlotResource<SomeType> the attacker does not own.
public fun steal_slot(victim_addr: address): storage_slot::StorageSlot<SomeType> {
    let bytes = std::bcs::to_bytes(&victim_addr);
    std::bcs::from_bytes<storage_slot::StorageSlot<SomeType>>(bytes)
}

public entry fun exploit(victim_addr: address) {
    let forged = steal_slot(victim_addr);
    // Mutates the victim's StorageSlotResource<SomeType> directly.
    *forged.borrow_mut() = attacker_controlled_value();
    // forged must still be consumed/destroyed since it has no `drop`,
    // e.g. via storage_slot::destroy, but the mutation has already
    // been committed to the victim's resource.
}
```
An integration test would confirm that after `exploit(victim_addr)` runs, the state proof / value returned for `victim_addr`'s `StorageSlotResource<SomeType>` has changed, despite the attacker never receiving that `StorageSlot<T>` handle through any legitimate `storage_slot::new`/transfer path. [5](#0-4)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/datastructures/storage_slot.move (L14-16)
```text
    struct StorageSlot<phantom T> has store {
        addr: address
    }
```

**File:** aptos-move/framework/aptos-framework/sources/datastructures/storage_slot.move (L18-22)
```text
    public fun new<T: store>(value: T): StorageSlot<T> {
        let unique_signer = object::create_unique_onchain_signer().generate_signer_for_extending();
        move_to(&unique_signer, StorageSlotResource { val: value });
        StorageSlot { addr: unique_signer.address_of() }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/datastructures/storage_slot.move (L33-36)
```text
    public fun borrow_mut<T: store>(self: &mut StorageSlot<T>): &mut T {
        assert!(std::features::is_storage_slot_natives_enabled(), ESTORAGE_SLOT_NATIVES_NOT_ENABLED);
        &mut self.borrow_storage_slot_resource_mut<T, StorageSlotResource<T>>().val
    }
```

**File:** aptos-move/framework/aptos-framework/sources/datastructures/storage_slot.move (L42-46)
```text
    public fun destroy<T: store>(self: StorageSlot<T>): T {
        let StorageSlot { addr } = self;
        let StorageSlotResource { val } = move_from<StorageSlotResource<T>>(addr);
        val
    }
```

**File:** aptos-move/framework/natives/src/storage_slot.rs (L100-124)
```rust
    // Get the address from StorageSlot.addr field
    let storage_slot_ref = safely_pop_arg!(args, StructRef);
    let addr = storage_slot_ref
        .borrow_field(0)?
        .value_as::<Reference>()?
        .read_ref()?
        .value_as::<AccountAddress>()?;

    // ty_args[1] is StorageSlotResource<T> - the type we want to borrow from global storage
    let storage_slot_resource_ty = &ty_args[1];

    // Borrow the resource mutably from global storage
    let (ref_val, num_bytes) = context
        .borrow_resource_mut(addr, storage_slot_resource_ty)
        .map_err(|err| {
            // Check if resource doesn't exist
            if err.major_status() == StatusCode::MISSING_DATA {
                SafeNativeError::abort_with_message(
                    ESTORAGE_SLOT_NOT_FOUND,
                    format!("StorageSlotResource at address {} not found", addr),
                )
            } else {
                err.into()
            }
        })?;
```
