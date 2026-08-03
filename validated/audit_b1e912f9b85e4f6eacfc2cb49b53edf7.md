No vulnerability found for this question.

**Analysis:** The premise of the question — that `property_map::update`/`update_typed` might exhibit non-deterministic behavior (in-place overwrite vs. remove+reinsert) when the property's type changes — does not hold. Both entry points in `aptos_token.move` delegate to the same single code path: [1](#0-0) 

Both `property_map::update` and `property_map::update_typed` route through `update_internal`, which performs exactly one deterministic operation regardless of whether the incoming `type` differs from the existing stored type: it asserts the map's existence, borrows the entry mutably, and overwrites it with a freshly constructed `PropertyValue { type, value }`: [2](#0-1) 

There is no branching logic in `update_internal` that depends on whether the new type matches the old type — it is a single unconditional struct assignment (`*old_value = PropertyValue { type, value }`) via `SimpleMap::borrow_mut`. There is no "remove+reinsert" code path at all in this function; that alternative described in the question does not exist in the implementation. Since there is only one deterministic execution path with no ordering ambiguity, the resulting `PropertyMap` entry's serialized bytes are fully determined by the final `(type, value)` pair passed in, independent of any prior type — there is no divergence to exploit, and no write-set ordering corruption is possible from this call.

The scope is also inherently limited to the single key within the caller-authorized token's own `PropertyMap` resource (gated by `authorized_borrow` and `are_properties_mutable`), so even a hypothetical inconsistency would not extend beyond that key's own committed state, and no proof/storage/accumulator binding is implicated.

### Citations

**File:** aptos-move/framework/aptos-token-objects/sources/aptos_token.move (L504-533)
```text
    public entry fun update_property<T: key>(
        creator: &signer,
        token: Object<T>,
        key: String,
        type: String,
        value: vector<u8>,
    ) acquires AptosCollection, AptosToken {
        let aptos_token = authorized_borrow(&token, creator);
        assert!(
            are_properties_mutable(token),
            error::permission_denied(EPROPERTIES_NOT_MUTABLE),
        );

        property_map::update(&aptos_token.property_mutator_ref, &key, type, value);
    }

    public entry fun update_typed_property<T: key, V: drop>(
        creator: &signer,
        token: Object<T>,
        key: String,
        value: V,
    ) acquires AptosCollection, AptosToken {
        let aptos_token = authorized_borrow(&token, creator);
        assert!(
            are_properties_mutable(token),
            error::permission_denied(EPROPERTIES_NOT_MUTABLE),
        );

        property_map::update_typed(&aptos_token.property_mutator_ref, &key, value);
    }
```

**File:** aptos-move/framework/aptos-token-objects/sources/property_map.move (L319-337)
```text
    /// Updates a property in place already bcs encoded
    public fun update(ref: &MutatorRef, key: &String, type: String, value: vector<u8>) acquires PropertyMap {
        let new_type = to_internal_type(type);
        validate_type(new_type, value);
        update_internal(ref, key, new_type, value);
    }

    /// Updates a property in place that is not already bcs encoded
    public fun update_typed<T: drop>(ref: &MutatorRef, key: &String, value: T) acquires PropertyMap {
        let type = type_info_to_internal_type<T>();
        update_internal(ref, key, type, bcs::to_bytes(&value));
    }

    inline fun update_internal(ref: &MutatorRef, key: &String, type: u8, value: vector<u8>) {
        assert_exists(ref.self);
        let property_map = &mut PropertyMap[ref.self];
        let old_value = property_map.inner.borrow_mut(key);
        *old_value = PropertyValue { type, value };
    }
```
