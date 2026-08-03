[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/framework/natives/src/cryptography/algebra/serialization.rs (L468-479)
```rust
        (Some(Structure::BN254Fr), Some(SerializationFormat::BN254FrLsb)) => {
            if bytes.len() != 32 {
                return Ok(smallvec![Value::bool(false), Value::u64(0)]);
            }
            ark_deserialize_internal!(
                context,
                bytes,
                ark_bn254::Fr,
                deserialize_uncompressed,
                ALGEBRA_ARK_BN254_FR_DESER
            )
        },
```

**File:** aptos-move/framework/natives/src/cryptography/algebra/serialization.rs (L480-494)
```rust
        (Some(Structure::BN254Fr), Some(SerializationFormat::BN254FrMsb)) => {
            if bytes.len() != 32 {
                return Ok(smallvec![Value::bool(false), Value::u64(0)]);
            }
            let mut bytes_copy: Vec<u8> = bytes.to_vec();
            bytes_copy.reverse();
            let bytes = bytes_copy.as_slice();
            ark_deserialize_internal!(
                context,
                bytes,
                ark_bn254::Fr,
                deserialize_uncompressed,
                ALGEBRA_ARK_BN254_FR_DESER
            )
        },
```

**File:** aptos-move/framework/aptos-stdlib/sources/cryptography/bls12381_algebra.move (L695-701)
```text
        let val_7 = from_u64<Fr>(7);
        let val_7_2nd = deserialize<Fr, FormatFrLsb>(&FR_VAL_7_SERIALIZED_LSB).extract();
        let val_7_3rd = deserialize<Fr, FormatFrMsb>(&FR_VAL_7_SERIALIZED_MSB).extract();
        assert!(eq(&val_7, &val_7_2nd), 1);
        assert!(eq(&val_7, &val_7_3rd), 1);
        assert!(FR_VAL_7_SERIALIZED_LSB == serialize<Fr, FormatFrLsb>(&val_7), 1);
        assert!(FR_VAL_7_SERIALIZED_MSB == serialize<Fr, FormatFrMsb>(&val_7), 1);
```

**File:** aptos-move/framework/aptos-stdlib/sources/cryptography/bn254_algebra.move (L670-676)
```text
        let val_7 = from_u64<Fr>(7);
        let val_7_2nd = deserialize<Fr, FormatFrLsb>(&FR_VAL_7_SERIALIZED_LSB).extract();
        let val_7_3rd = deserialize<Fr, FormatFrMsb>(&FR_VAL_7_SERIALIZED_MSB).extract();
        assert!(eq(&val_7, &val_7_2nd), 1);
        assert!(eq(&val_7, &val_7_3rd), 1);
        assert!(FR_VAL_7_SERIALIZED_LSB == serialize<Fr, FormatFrLsb>(&val_7), 1);
        assert!(FR_VAL_7_SERIALIZED_MSB == serialize<Fr, FormatFrMsb>(&val_7), 1);
```
