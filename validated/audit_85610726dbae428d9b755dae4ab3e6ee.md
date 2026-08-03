[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/natives/src/cryptography/ristretto255_point.rs (L217-230)
```rust
/// If 'bytes' canonically-encode a valid RistrettoPoint, returns the point.  Otherwise, returns None.
fn decompress_maybe_non_canonical_point_bytes(
    context: &mut SafeNativeContext,
    bytes: Vec<u8>,
) -> SafeNativeResult<Option<RistrettoPoint>> {
    context.charge(RISTRETTO255_POINT_DECOMPRESS * NumArgs::one())?;

    let compressed = match compressed_point_from_bytes(bytes) {
        Some(point) => point,
        None => return Ok(None),
    };

    Ok(compressed.decompress())
}
```

**File:** aptos-move/framework/natives/src/cryptography/ristretto255_point.rs (L733-739)
```rust
/// Checks if `COMPRESSED_POINT_NUM_BYTES` bytes were given as input and, if so, returns Some(CompressedRistretto).
fn compressed_point_from_bytes(bytes: Vec<u8>) -> Option<CompressedRistretto> {
    match <[u8; COMPRESSED_POINT_NUM_BYTES]>::try_from(bytes) {
        Ok(slice) => Some(CompressedRistretto(slice)),
        Err(_) => None,
    }
}
```

**File:** aptos-move/framework/natives/src/cryptography/ristretto255.rs (L42-45)
```rust
        (
            "point_decompress_internal",
            ristretto255_point::native_point_decompress,
        ),
```
