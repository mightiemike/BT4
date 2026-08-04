[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-token-objects/sources/property_map.move (L39-49)
```text
    // PropertyValue::type
    const BOOL: u8 = 0;
    const U8: u8 = 1;
    const U16: u8 = 2;
    const U32: u8 = 3;
    const U64: u8 = 4;
    const U128: u8 = 5;
    const U256: u8 = 6;
    const ADDRESS: u8 = 7;
    const BYTE_VECTOR: u8 = 8;
    const STRING: u8 = 9;
```

**File:** aptos-move/framework/aptos-token-objects/sources/property_map.move (L59-63)
```text
    /// A typed value for the `PropertyMap` to ensure that typing is always consistent
    struct PropertyValue has drop, store {
        type: u8,
        value: vector<u8>,
    }
```

**File:** aptos-move/framework/aptos-token-objects/sources/property_map.move (L241-247)
```text
        let (type, value) = read(object, key);
        assert!(
            type == type_info::type_name<V>(),
            error::invalid_argument(ETYPE_MISMATCH),
        );
        value
    }
```
