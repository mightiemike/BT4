No vulnerability found for this question.

**Reasoning:**

`from_bcs::to_address` is a thin wrapper around the native deserializer `from_bytes<address>`, which simply requires that the input `vector<u8>` be exactly 32 bytes (the BCS-encoded length of `address`) and deserializes it byte-for-byte. [1](#0-0)  There is no injectivity or "collision" bug possible at this layer: any given 32-byte input maps deterministically to exactly one address, and the function has no branching or reinterpretation logic that unprivileged input could abuse to redirect the deserialization result.

Inside `account::create_resource_address`, the input to `from_bcs::to_address` is not an arbitrary attacker-controlled `vector<u8>` — it is always the fixed-size 32-byte output of `hash::sha3_256(bytes)`, where `bytes` is `bcs::to_bytes(source) || seed || DERIVE_RESOURCE_ACCOUNT_SCHEME`. [2](#0-1)  Since SHA3-256 output is always exactly 32 bytes, `to_address` will always successfully and deterministically decode it — there is no ambiguity or alternate encoding for the attacker to exploit at the BCS-deserialization boundary itself.

The only way two distinct `(source, seed)` pairs could produce the same resource address is through an actual SHA3-256 hash collision (or a second-preimage), which is a cryptographic-strength assumption explicitly acknowledged in the code's own documentation: "The probability of a collision ... is less than `(1/2)^(256)`." [3](#0-2)  This is also reflected in the formal spec, which treats `create_resource_address` as an abstract/opaque function that never aborts and whose collision resistance is assumed rather than proven. [4](#0-3)  The same domain-separated hash-derivation pattern (with distinct scheme bytes like `DERIVE_RESOURCE_ACCOUNT_SCHEME`, `OBJECT_FROM_SEED_ADDRESS_SCHEME`, etc.) is used consistently across `object::create_object_address` and other derivation helpers to prevent cross-scheme collisions between different derivation contexts. [5](#0-4) [6](#0-5) 

There is no code path where unprivileged input can bypass the hash function, craft a raw 32-byte vector directly consumed by `create_resource_address`, or otherwise force `from_bcs::to_address` to reinterpret bytes into an unintended address independent of the SHA3-256 output. The scenario described depends entirely on breaking a cryptographic hash function's collision resistance, which is an assumed security property of the algorithm, not an implementation defect in `from_bcs.move`, `account.move`, or the write-set/storage handoff logic in scope for this review.

### Citations

**File:** aptos-move/framework/aptos-stdlib/sources/from_bcs.move (L47-49)
```text
    public fun to_address(v: vector<u8>): address {
        from_bytes<address>(v)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/account/account.move (L1140-1145)
```text
    public fun create_resource_address(source: &address, seed: vector<u8>): address {
        let bytes = bcs::to_bytes(source);
        bytes.append(seed);
        bytes.push_back(DERIVE_RESOURCE_ACCOUNT_SCHEME);
        from_bcs::to_address(hash::sha3_256(bytes))
    }
```

**File:** aptos-move/framework/aptos-framework/sources/account/account.move (L1153-1155)
```text
    /// yet to execute any transactions and that the `Account::signer_capability_offer::for` is none. The probability of a
    /// collision where someone has legitimately produced a private key that maps to a resource account address is less
    /// than `(1/2)^(256)`.
```

**File:** aptos-move/framework/aptos-framework/sources/account/account.spec.move (L596-605)
```text
    spec create_resource_address(source: &address, seed: vector<u8>): address {
        pragma opaque;
        pragma aborts_if_is_strict = false;
        // This function should not abort assuming the result of `sha3_256` is deserializable into an address.
        aborts_if [abstract] false;
        ensures [abstract] result == spec_create_resource_address(source, seed);
        ensures [abstract] source != result; // We can assume that the derived resource account does not equal to `source`
    }

    spec fun spec_create_resource_address(source: address, seed: vector<u8>): address;
```

**File:** aptos-move/framework/aptos-framework/sources/object.move (L90-95)
```text
    /// Scheme identifier used to generate an object's address `obj_addr` from the creator's `source_addr` and a `seed` as:
    ///     obj_addr = sha3_256(source_addr | seed | 0xFE).
    ///
    /// This 0xFE constant serves as a domain separation tag to prevent existing authentication key and resource account
    /// derivation to produce an object address.
    const OBJECT_FROM_SEED_ADDRESS_SCHEME: u8 = 0xFE;
```

**File:** aptos-move/framework/aptos-framework/sources/object.move (L219-224)
```text
    public fun create_object_address(source: &address, seed: vector<u8>): address {
        let bytes = bcs::to_bytes(source);
        bytes.append(seed);
        bytes.push_back(OBJECT_FROM_SEED_ADDRESS_SCHEME);
        from_bcs::to_address(hash::sha3_256(bytes))
    }
```
