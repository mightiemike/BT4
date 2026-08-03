[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** aptos-move/framework/aptos-stdlib/sources/bcs_stream.move (L24-29)
```text
    struct BCSStream has drop {
        /// Byte buffer containing the serialized data.
        data: vector<u8>,
        /// Cursor indicating the current position in the byte buffer.
        cur: u64,
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/bcs_stream.move (L269-278)
```text
    public inline fun deserialize_vector<E>(stream: &mut BCSStream, elem_deserializer: |&mut BCSStream| E): vector<E> {
        let len = deserialize_uleb128(stream);
        let v = vector::empty();

        for (i in 0..len) {
            v.push_back(elem_deserializer(stream));
        };

        v
    }
```

**File:** aptos-move/move-examples/bcs-stream/sources/tests/tests.move (L248-256)
```text
    #[test]
    #[expected_failure(abort_code = 0x020002, location = bcs_stream::bcs_stream)]
    fun test_vector_not_enough_items() {
        let data = x"FFFFFFFFFFFFFFFFFF01";
        let stream = bcs_stream::new(data);
        bcs_stream::deserialize_vector(&mut stream, |stream| {
            bcs_stream::deserialize_u8(stream)
        });
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/cryptography/multi_key.spec.move (L6-15)
```text
    spec new_multi_key_from_single_keys(
        single_keys: vector<single_key::AnyPublicKey>,
        signatures_required: u8
    ): MultiKey {
        pragma opaque;
        aborts_if len(single_keys) == 0;
        aborts_if len(single_keys) > MAX_NUMBER_OF_PUBLIC_KEYS;
        aborts_if (signatures_required as u64) > len(single_keys);
        ensures result == MultiKey { public_keys: single_keys, signatures_required };
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/cryptography/single_key.spec.move (L11-21)
```text
    spec from_ed25519_public_key_unvalidated(pk: ed25519::UnvalidatedPublicKey): AnyPublicKey {
        pragma opaque;
        aborts_if false;
        ensures result == AnyPublicKey::Ed25519 { pk };
    }

    spec to_authentication_key(self: &AnyPublicKey): vector<u8> {
        pragma opaque;
        aborts_if false;
        ensures len(result) == 32;
    }
```

**File:** aptos-move/framework/aptos-framework/sources/account/account.move (L477-481)
```text
        } else if (scheme == SINGLE_KEY_SCHEME) {
            new_auth_key = single_key::new_public_key_from_bytes(new_public_key_bytes).to_authentication_key();
        } else if (scheme == MULTI_KEY_SCHEME) {
            new_auth_key = multi_key::new_public_key_from_bytes(new_public_key_bytes).to_authentication_key();
        } else {
```
