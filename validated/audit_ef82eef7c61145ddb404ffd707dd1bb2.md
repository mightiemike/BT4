No usage in mainnet framework, storage, proof, or consensus code — confirmed no meaningful call sites tying this function to accumulator/proof/state-commitment logic.

## Analysis

Walking through `exp_raw` at `aptos-move/framework/aptos-stdlib/sources/math_fixed.move`:

- `x` is a `u128` cast from a `FixedPoint32`'s raw value, which is itself a `u64`, so the actual maximum possible `x` is `2^64 - 1`. [1](#0-0) 
- `shift_long = x / LN2` (line 57) with `LN2 = 2977044472`, and the code aborts if `shift_long > 31` (line 58). [2](#0-1) 
- When `shift_long == 31` (the maximum allowed, non-aborting value), `shift = 31`. [3](#0-2) 
- `power` is the result of `pow_raw(roottwo, exponent)` where `exponent = remainder / 595528` and `remainder < LN2 ≈ 2977044472`, so `exponent < 4999`. The test `test_pow` confirms `pow_raw(roottwue, 4999)` is approximately `1 << 33`, i.e., `power` is bounded near `2^33`.
<invoke name="codebase_search">
<parameter name="query">placeholder</parameter>
</invoke>

### Citations

**File:** aptos-move/framework/aptos-stdlib/sources/math_fixed.move (L22-25)
```text
    public fun exp(x: FixedPoint32): FixedPoint32 {
        let raw_value = (x.get_raw_value() as u128);
        fixed_point32::create_from_raw_value((exp_raw(raw_value) as u64))
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/math_fixed.move (L56-59)
```text
        // exp(x / 2^32) = 2^(x / (2^32 * ln(2))) = 2^(floor(x / (2^32 * ln(2))) + frac(x / (2^32 * ln(2))))
        let shift_long = x / LN2;
        assert!(shift_long <= 31, std::error::invalid_state(EOVERFLOW_EXP));
        let shift = (shift_long as u8);
```
