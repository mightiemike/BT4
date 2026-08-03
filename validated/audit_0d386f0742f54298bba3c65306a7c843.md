[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-stdlib/sources/math_fixed.move (L91-92)
```text
        ((res >> 32) as u128)
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/math_fixed.move (L120-125)
```text
    #[test]
    public entry fun test_pow() {
        // We use the case of exp
        let result = pow_raw(4295562865, 4999);
        assert_approx_the_same(result,  1 << 33, 6);
    }
```
