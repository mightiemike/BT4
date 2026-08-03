[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-stdlib/sources/cryptography/ristretto255_bulletproofs.move (L105-118)
```text
    public fun verify_range_proof(
        com: &RistrettoPoint,
        val_base: &RistrettoPoint, rand_base: &RistrettoPoint,
        proof: &RangeProof, num_bits: u64, dst: vector<u8>): bool
    {
        assert!(features::bulletproofs_enabled(), error::invalid_state(E_NATIVE_FUN_NOT_AVAILABLE));
        assert!(dst.length() <= 256, error::invalid_argument(E_DST_TOO_LONG));

        verify_range_proof_internal(
            ristretto255::point_to_bytes(&ristretto255::point_compress(com)),
            val_base, rand_base,
            proof.bytes, num_bits, dst
        )
    }
```
