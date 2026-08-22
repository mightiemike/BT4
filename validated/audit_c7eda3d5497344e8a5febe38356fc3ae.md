## Finding

### Title
Missing low-S (signature malleability) check in the `ECRecover` precompiled contract - (`actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The reported bug describes a Solidity contract that recovers a signer from `(r, s, v)` without confining `s` to the "canonical" lower half of the secp256k1 curve order, enabling signature malleability. The same root-cause pattern exists in java-tron's TVM `ECRecover` precompiled contract (address `0x01`), which every deployed smart contract can reach through the Solidity `ecrecover()` builtin.

### Finding Description
`PrecompiledContracts.ECRecover.execute()` parses `r`, `s`, `v` from the input and calls `signature.validateComponents()` before recovering the address: [1](#0-0) 

That validation is implemented in `ECDSASignature.validateComponents(BigInteger r, BigInteger s, byte v)`: [2](#0-1) 

This check only enforces `1 <= s < SECP256K1N` — it does **not** enforce the canonical/low-S rule `s <= N/2` that the external report calls out (`0 < s < secp256k1n/2 + 1`). The class already defines the correct half-order constant, `HALF_CURVE_ORDER`, and even uses it for canonicalizing signatures created via `toCanonicalised()` after `doSign()`: [3](#0-2) [4](#0-3) 

However `HALF_CURVE_ORDER` is never consulted inside `validateComponents()`, so `ECRecover` (and any other caller of `validateComponents`) will happily accept the "upper-half" variant `N - s` as valid, exactly the malleability class in the external report. For any signature `(r, s, v)` that verifies for address `A`, the flipped signature `(r, N-s, v')` also verifies for `A`, producing two distinct byte encodings of a signature over the same message/signer.

### Impact Explanation
Any deployed TVM smart contract that calls Solidity's `ecrecover()` (compiled down to the `ECRecover` precompile) and relies on the signature bytes themselves as a uniqueness/anti-replay token — e.g., meta-transaction/permit-style contracts that mark a signature (or its hash) as "used" rather than tracking a nonce — can be bypassed: an attacker can derive a second valid signature for an already-consumed authorization and resubmit it, defeating the replay/one-time-use guarantee and enabling double-execution of a signed authorization (e.g., double transfer, double permit-approval). This is reachable from any ordinary contract-call transaction (`TriggerSmartContract`), i.e., from an anonymous broadcast transaction, not a privileged actor.

### Likelihood Explanation
Likelihood is contract-pattern dependent: it only affects contracts on TVM that (a) use `ecrecover` and (b) use the raw signature (not just the recovered signer address plus an explicit nonce) as the replay-protection key. This is a known, documented anti-pattern (this is precisely the class of bug flagged in the referenced ERC7540 report), and such patterns exist in the wild for gasless/meta-tx and permit-style contracts, so likelihood is real but conditioned on contract design rather than an unconditional protocol break. It does not affect java-tron's own consensus signature checks (`TransactionCapsule.checkWeight`), since those never treat the signature bytes as a replay key.

### Recommendation
Enforce the canonical low-S rule inside `ECDSASignature.validateComponents` (or add an explicit check in `PrecompiledContracts.ECRecover.execute`) using the already-defined `HALF_CURVE_ORDER` constant, e.g. reject when `s.compareTo(HALF_CURVE_ORDER) > 0`, mirroring the check already used in `toCanonicalised()`.

### Proof of Concept
1. Deploy (conceptually) a TVM contract using a permit/meta-tx pattern that maps `keccak256(signature)` (or the raw signature) to a "used" flag instead of using an incrementing nonce.
2. A user signs message `m`, producing canonical signature `(r, s, v)`, and submits a `TriggerSmartContract` transaction that calls the permit function, marking the signature hash as used.
3. Attacker computes the malleable counterpart `s' = SECP256K1N - s` (flipping `v` accordingly) and calls `ECRecover`/`ecrecover` with `(r, s', v')`.
4. `ECDSASignature.validateComponents` at `ECKey.java:923-941` accepts `s'` because it only checks `1 <= s' < SECP256K1N`, not `s' <= N/2`; `recoverPubBytesFromSignature` returns the same signer address.
5. The contract sees a "new" signature hash for the same authorized message and permits re-execution, breaking the intended one-time-use guarantee.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L616-621)
```java
        SignatureInterface signature = SignUtils.fromComponents(r, s, v[31]
            , CommonParameter.getInstance().isECKeyCryptoEngine());
        if (validateV(v) && signature.validateComponents()) {
          out = new DataWord(SignUtils.signatureToAddress(h, signature
              , CommonParameter.getInstance().isECKeyCryptoEngine()));
        }
```

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L78-93)
```java

  public static final BigInteger HALF_CURVE_ORDER;
  private static final BigInteger SECP256K1N =
      new BigInteger("fffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141", 16);
  private static final SecureRandom secureRandom;
  private static final long serialVersionUID = -728224901792295832L;

  static {
    // All clients must agree on the curve to use by agreement.
    X9ECParameters params = SECNamedCurves.getByName("secp256k1");
    CURVE = new ECDomainParameters(params.getCurve(), params.getG(),
        params.getN(), params.getH());
    CURVE_SPEC = new ECParameterSpec(params.getCurve(), params.getG(),
        params.getN(), params.getH());
    HALF_CURVE_ORDER = params.getN().shiftRight(1);
    secureRandom = new SecureRandom();
```

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L923-941)
```java
    public static boolean validateComponents(BigInteger r, BigInteger s,
        byte v) {

      if (v != 27 && v != 28) {
        return false;
      }

      if (BIUtil.isLessThan(r, BigInteger.ONE)) {
        return false;
      }
      if (BIUtil.isLessThan(s, BigInteger.ONE)) {
        return false;
      }

      if (!BIUtil.isLessThan(r, SECP256K1N)) {
        return false;
      }
      return BIUtil.isLessThan(s, SECP256K1N);
    }
```

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L948-963)
```java
    public ECDSASignature toCanonicalised() {
      if (s.compareTo(HALF_CURVE_ORDER) > 0) {
        // The order of the curve is the number of valid points that
        // exist on that curve. If S is in the upper
        // half of the number of valid points, then bring it back to
        // the lower half. Otherwise, imagine that
        //    N = 10
        //    s = 8, so (-8 % 10 == 2) thus both (r, 8) and (r, 2)
        // are valid solutions.
        //    10 - 8 == 2, giving us always the latter solution,
        // which is canonical.
        return new ECDSASignature(r, CURVE.getN().subtract(s));
      } else {
        return this;
      }
    }
```
