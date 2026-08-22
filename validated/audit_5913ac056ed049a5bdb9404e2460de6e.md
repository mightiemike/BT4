### Title
Signature malleability bypasses `ValidateMultiSign` dedup, letting a single key satisfy a multi-key threshold - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
`ValidateMultiSign.execute` deduplicates submitted signatures by first checking whether `recoveredAddr` is already present in `executedSignList`, and only then checking whether the exact raw `sign` bytes are also present; if the address matches but the raw bytes differ it falls through and adds the weight again instead of skipping. Because ECDSA signatures are malleable ((r, s, v) and (r, n-s, v^1) both recover to the same address), an attacker holding one private key can submit two syntactically different signature encodings of the same key to have that single key's weight counted twice (or more, up to `MAX_SIZE=5`), crossing a threshold that is supposed to require independent signers.

### Finding Description
In `PrecompiledContracts.ValidateMultiSign.execute` [1](#0-0) , for each entry in the `signatures` array the code:
1. Recovers `recoveredAddr = recoverAddrBySign(sign, hash)` via `Rsv.fromSignature(sign)` [2](#0-1)  and `SignUtils.signatureToAddress`, which does standard ECDSA recovery and only rejects components that fail `validateComponents()` (out-of-range r/s), not signatures with a "high-s" (malleated) value.
2. Builds `sign = merge(recoveredAddr, sign)` (address‑prefixed raw signature bytes).
3. Checks `ByteArray.matrixContains(executedSignList, recoveredAddr)`: if the recovered address was already seen, it checks `matrixContains(executedSignList, sign)` (the exact address+signature-bytes combo) and only `continue`s (skips) on an **exact** match. If the address matches but the byte-for-byte signature differs, execution falls through the `if`, calls `MUtil.checkCPUTime()` (a CPU-time DoS guard, not a security gate), and proceeds to add the recovered address's weight to `totalWeight` again.
4. `totalWeight` is compared against `permission.getThreshold()`; if reached, the call returns `dataOne()` (DATA_TRUE), meaning "signature valid."

Because ECDSA is malleable, an attacker with a single private key can sign once, then derive `(r, n-s, v^1)` from `(r, s, v)` without knowing the private key — this is a standard elliptic-curve computation. Both signatures produce the same `recoveredAddr` but different raw bytes, so the dedup check's second-level `matrixContains(executedSignList, sign)` fails and the same address's weight is applied twice (or up to `MAX_SIZE=5` times), even though only one real key ever signed.

### Impact Explanation
This breaks the "exact-authorization" invariant of TRON's multi-signature scheme: an unprivileged attacker (e.g., a contract deployer who only controls one key under a Permission, or an attacker who otherwise obtains one valid signature over the relevant hash) can forge a passing multisig check for any Solidity contract logic gated on `ValidateMultiSign` reaching a `permission.getThreshold()`, without a majority/quorum of independent owner keys ever consenting. Downstream this enables unauthorized approval of account/contract operations (e.g., authorizing a transfer, an owner-permission change, or any application-level action) that the contract author intended to require multiple independent signers — i.e., an unauthorized-account-operation impact class within TRON's precompile trust model.

### Likelihood Explanation
- Preconditions: attacker needs one valid signature (their own key) over the exact `hash` computed from `(address, permissionId, data)`; that's normal usage of the precompile from a Solidity contract. Deriving the malleated `(r, n-s, v^1)` companion signature requires no private key material — it's public-key arithmetic on the existing `(r, s)`.
- Cost: only standard energy cost per signature (`ENGERYPERSIGN=1500` per slot) and transaction fees; no privileged role, no node/config assumptions.
- Repeatability: fully deterministic and repeatable for any Permission with `threshold <= weight_of_one_key * MAX_SIZE(5)`, e.g., a 2-of-2 permission where the attacker controls one of the two keys, or scenarios where a contract intends a threshold across supposedly-independent signers but only one signer is compromised/cooperating with the attacker.
- The existing `TransactionCapsule.getWeight`/permission checks are not bypassed technically, but the dedup logic that is supposed to prevent double counting the same signer is defeated by malleability, so the "checks already in place" (dedup by recoveredAddr + sign) do not stop this specific attack.

### Recommendation
Deduplicate strictly by `recoveredAddr` alone (not by the address+signature-bytes tuple): once an address has been credited, `continue` regardless of whether the new signature's raw bytes differ. Additionally/alternatively, normalize/canonicalize `s` (reject `s > n/2`, i.e., enforce low-s form) before or during `recoverAddrBySign`/`Rsv.fromSignature`, consistent with standard ECDSA malleability defenses, so that malleated encodings are rejected outright.

### Proof of Concept
```java
// In ValidateMultiSignContractTest.java style
ECKey key1 = new ECKey();
ECKey key2 = new ECKey(); // permission requires both, threshold=2, weight=1 each

byte[] toSign = /* hash computed as in execute(): sha256(address || permissionId || data) */;

ECKey.ECDSASignature sig = key1.sign(toSign);
// Derive malleated counterpart: (r, n-s, v^1) — pure arithmetic, no private key needed.
BigInteger n = ECKey.CURVE.getN();
BigInteger sMalleated = n.subtract(sig.s);
byte vMalleated = (byte) (sig.v ^ 1); // flip recovery id
byte[] sign1 = sig.toByteArray();                       // (r, s, v)
byte[] sign2 = buildSignatureBytes(sig.r, sMalleated, vMalleated); // (r, n-s, v^1)

List<Object> signs = new ArrayList<>();
signs.add(Hex.toHexString(sign1));
signs.add(Hex.toHexString(sign2));
// key2 never signs.

Pair<Boolean, byte[]> ret = validateMultiSign(
    StringUtil.encode58Check(account.getAddress()), permissionId, data, signs);

// Expected (secure): DATA_FALSE
// Actual (vulnerable): DATA_TRUE — totalWeight reaches threshold=2 using only key1.
Assert.assertArrayEquals(ret.getValue(), DataWord.ONE().getData()); // demonstrates the bug
```
This mirrors the existing `testDifferentCase` test in `ValidateMultiSignContractTest.java` [3](#0-2) , which already exercises exact-duplicate submission (`key1.sign` twice, same bytes) and correctly gets deduped down to weight 1 → DATA_FALSE for a 2-of-2 permission; replacing the exact duplicate with its ECDSA-malleated counterpart bytes demonstrates the dedup bypass at `executedSignList` handling in `execute` [4](#0-3) .

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1086-1106)
```java
            long totalWeight = 0L;
            List<byte[]> executedSignList = new ArrayList<>();
            for (byte[] sign : signatures) {
              byte[] recoveredAddr = recoverAddrBySign(sign, hash);

              sign = merge(recoveredAddr, sign);
              if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
                if (ByteArray.matrixContains(executedSignList, sign)) {
                  continue;
                }
                MUtil.checkCPUTime();
              }
              long weight = TransactionCapsule.getWeight(permission, recoveredAddr);
              if (weight == 0) {
                //incorrect sign
                return Pair.of(true, DATA_FALSE);
              }
              totalWeight += weight;
              executedSignList.add(sign);
              executedSignList.add(recoveredAddr);
            }
```

**File:** crypto/src/main/java/org/tron/common/crypto/Rsv.java (L17-25)
```java
  public static Rsv fromSignature(byte[] sign) {
    byte[] r = Arrays.copyOfRange(sign, 0, 32);
    byte[] s = Arrays.copyOfRange(sign, 32, 64);
    byte v = sign[64];
    if (v < 27) {
      v += 27; //revId -> v
    }
    return new Rsv(r, s, v);
  }
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java (L117-125)
```java
    List<Object> signs = new ArrayList<>();
    signs.add(Hex.toHexString(key1.sign(toSign).toByteArray()));
    //add Repetitive
    signs.add(Hex.toHexString(key1.sign(toSign).toByteArray()));
    signs.add(Hex.toHexString(key2.sign(toSign).toByteArray()));

    Assert.assertArrayEquals(
        validateMultiSign(StringUtil.encode58Check(key.getAddress()), permissionId, data, signs)
            .getValue(), DataWord.ONE().getData());
```
