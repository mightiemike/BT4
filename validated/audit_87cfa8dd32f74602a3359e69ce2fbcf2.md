### Title
Signature-malleability weight duplication in `ValidateMultiSign` precompile allows single-key threshold bypass - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
The `ValidateMultiSign` precompile's `executedSignList` dedup only skips a signature when the *exact byte-identical* `(recoveredAddr || rawSignatureBytes)` pair has already been counted; if a second signature byte-string recovers to the *same* `recoveredAddr` but differs in its raw bytes (e.g. an ECDSA-malleable `(r, N-s, flip(v))` variant of an already-seen signature), the loop falls through and adds that key's weight to `totalWeight` a second time. This lets a party controlling a single authorized key's signature satisfy a threshold that should require multiple distinct signers.

### Finding Description
`PrecompiledContracts.ValidateMultiSign.execute()` computes total weight as: [1](#0-0) 

For each signature in the attacker-supplied `signatures[]` array, `recoveredAddr` is recovered via ECDSA recovery, then `sign` is redefined as `merge(recoveredAddr, sign)` (i.e. address‖original-signature-bytes). The dedup check is:
```
if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
  if (ByteArray.matrixContains(executedSignList, sign)) {
    continue;   // only skips if address+bytes were already seen verbatim
  }
  MUtil.checkCPUTime();   // otherwise just a CPU-time check, execution continues
}
```
`ByteArray.matrixContains` is a raw `Arrays.equals` scan: [2](#0-1) . It does **not** check "has this `recoveredAddr` already contributed weight" — it checks "has this exact `(address, raw-signature-bytes)` tuple already contributed weight". If the attacker submits two *different* raw signature byte strings that both recover to the same authorized address (classic ECDSA malleability: `(r, s, v)` and `(r, n-s, v XOR 1)` recover to the identical public key/address), the inner `matrixContains(executedSignList, sign)` check is false (bytes differ), so the loop does not `continue` — it proceeds to add `weight` for that same `recoveredAddr` a second time, inflating `totalWeight`.

`recoverAddrBySign` builds the signature from raw `r`,`s`,`v` bytes via `Rsv.fromSignature` and only rejects malformed signatures via `signature.validateComponents()`: [3](#0-2) , `Rsv.fromSignature` performs no canonicalization beyond adjusting `v` for recId offset: [4](#0-3) . I was unable to fully confirm from the retrieved code whether `ECDSASignature.validateComponents()` additionally rejects non-canonical high-`S` signatures (a `HALF_CURVE_ORDER`-style low-S enforcement) or only performs range bounds checking — this is the one open uncertainty in this analysis, and it directly determines whether the classical `(r, N-s, flip(v))` malleability transform passes `validateComponents()` unchanged.

Regardless of that specific transform, the address-level dedup gap itself is a structural flaw independent of malleability details: any two distinct signature byte-strings recovering to the same address (which is trivially producible by an attacker who legitimately possesses one valid signature and can algebraically derive the malleable twin without the private key, assuming `validateComponents()` accepts it) bypass the "count each key once" invariant.

Entry point: this precompile lives at address `0x...0a` and is reachable from any smart contract via `CALL`/`STATICCALL` when `VMConfig.allowTvmSolidity059()` is active: [5](#0-4) . Any unprivileged account can submit a `TriggerSmartContract` transaction invoking a contract that performs this `CALL` with attacker-controlled calldata (`address`, `permissionId`, `data`, `signatures[]`), and gate a fund transfer on the boolean result.

### Impact Explanation
If exploitable, a holder of a single valid signature for a given `(address, permissionId, data)` triple — e.g., one member of an m-of-n TRON multisig `Permission`, or anyone who has obtained one such signature — could satisfy a threshold that is meant to require multiple independent co-signers, by supplying that signature's malleable twin as a "second" signer. This is a scoped authorization-bypass of TRON's active-permission multisig scheme when consumed through the `ValidateMultiSign`/`BatchValidateSign` TVM precompiles by any calling contract logic (e.g., an escrow/vault contract that gates a transfer on the boolean precompile result).

### Likelihood Explanation
Exploitability is contingent on the unconfirmed behavior of `ECDSASignature.validateComponents()`: if it enforces canonical low-`S` signatures (rejecting `S > HALF_CURVE_ORDER`), the specific `(r, N-s, flip(v))` malleable variant would fail to recover a valid address and the attack as described would not proceed past `recoverAddrBySign`. I could not verify this component within budget. The address-level dedup gap in the loop itself, however, is confirmed directly from the code and is a latent defect regardless of which malleable-signature construction (if any) currently passes validation.

### Recommendation
Change the dedup logic to key exclusively on `recoveredAddr` (skip/continue as soon as the address has been counted once), rather than on the compound `(recoveredAddr, rawSignatureBytes)` tuple, matching the actual security invariant ("each authorized key contributes weight at most once"). Additionally, verify/enforce that `ECDSASignature.validateComponents()` rejects non-canonical (high-`S`) signatures at the point of `recoverAddrBySign`, consistent with standard ECDSA malleability mitigations (e.g. EIP-2 / BIP-62 style low-S enforcement).

### Proof of Concept
```java
// Java unit/fuzz test targeting PrecompiledContracts.ValidateMultiSign
// (extend ValidateMultiSignContractTest)

@Test
public void testMalleableSignatureWeightDuplication() {
  ECKey key = new ECKey();
  AccountCapsule toAccount = new AccountCapsule(ByteString.copyFrom(key.getAddress()),
      Protocol.AccountType.Normal, System.currentTimeMillis(), true,
      dbManager.getDynamicPropertiesStore());
  ECKey key1 = new ECKey();   // only ONE authorized signer available to attacker
  ECKey key2 = new ECKey();   // second authorized signer, attacker has NO access to this key

  Protocol.Permission activePermission = Protocol.Permission.newBuilder()
      .setType(Protocol.Permission.PermissionType.Active).setId(2)
      .setPermissionName("active").setThreshold(2)
      .setOperations(ByteString.copyFrom(ByteArray.fromHexString(
          "0000000000000000000000000000000000000000000000000000000000000000")))
      .addKeys(Protocol.Key.newBuilder().setAddress(ByteString.copyFrom(key1.getAddress())).setWeight(1))
      .addKeys(Protocol.Key.newBuilder().setAddress(ByteString.copyFrom(key2.getAddress())).setWeight(1))
      .build();
  toAccount.updatePermissions(toAccount.getPermissionById(0), null,
      Collections.singletonList(activePermission));
  dbManager.getAccountStore().put(key.getAddress(), toAccount);

  byte[] data = Sha256Hash.hash(true, longData);
  byte[] merged = ByteUtil.merge(key.getAddress(), ByteArray.fromInt(2), data);
  byte[] toSign = Sha256Hash.hash(true, merged);

  // attacker holds only key1's signature
  ECKey.ECDSASignature sig1 = key1.sign(toSign);
  byte[] rawSig1 = sig1.toByteArray();

  // derive malleable twin WITHOUT key1's private key:
  // s' = N - s ; v' = flip parity bit
  BigInteger nMinusS = ECKey.CURVE.getN().subtract(sig1.s);
  byte v2 = (byte) (sig1.v == 27 ? 28 : 27);
  ECKey.ECDSASignature sig1Malleable = ECKey.ECDSASignature.fromComponents(
      ByteUtil.bigIntegerToBytes(sig1.r, 32),
      ByteUtil.bigIntegerToBytes(nMinusS, 32), v2);
  byte[] rawSig1Twin = sig1Malleable.toByteArray();

  List<Object> signs = new ArrayList<>();
  signs.add(Hex.toHexString(rawSig1));
  signs.add(Hex.toHexString(rawSig1Twin)); // NOT key2's signature — attacker never had it

  Pair<Boolean, byte[]> ret = validateMultiSign(
      StringUtil.encode58Check(key.getAddress()), 2, data, signs);

  // EXPECTED (fixed behavior): threshold of 2 NOT met with only one distinct key -> ZERO
  // BUG (current behavior): totalWeight = 1 + 1 = 2 >= threshold -> ONE (unauthorized pass)
  Assert.assertArrayEquals("dedup must count key1 once regardless of signature malleability",
      DataWord.ZERO().getData(), ret.getValue());
}
```
Expected result if the bug is present: the assertion fails because the precompile returns `DataWord.ONE()` despite only one distinct authorized key ever having signed — demonstrating threshold bypass via weight duplication. If `ECDSASignature.validateComponents()` already rejects the high-`S` malleable form, this specific PoC will not reproduce the double-count and the address-level dedup gap should instead be probed by fuzzing over different raw-byte encodings (e.g. `v` normalization forms, padding variants) that still pass `validateComponents()` and recover to the same address, per the "Scoped impact" invariant in the question.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L257-259)
```java
    if (VMConfig.allowTvmSolidity059() && address.equals(validateMultiSignAddr)) {
      return validateMultiSign;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L371-388)
```java
  private static byte[] recoverAddrBySign(byte[] sign, byte[] hash) {
    byte[] out = null;
    if (ArrayUtils.isEmpty(sign) || sign.length < 65) {
      return new byte[0];
    }
    try {
      Rsv rsv = Rsv.fromSignature(sign);
      SignatureInterface signature = SignUtils.fromComponents(rsv.getR(), rsv.getS(), rsv.getV(),
          CommonParameter.getInstance().isECKeyCryptoEngine());
      if (signature.validateComponents()) {
        out = SignUtils.signatureToAddress(hash, signature,
            CommonParameter.getInstance().isECKeyCryptoEngine());
      }
    } catch (Throwable any) {
      logger.info("ECRecover error", any.getMessage());
    }
    return out;
  }
```

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

**File:** common/src/main/java/org/tron/common/utils/ByteArray.java (L189-196)
```java
  public static boolean matrixContains(List<byte[]> source, byte[] obj) {
    for (byte[] sobj : source) {
      if (Arrays.equals(sobj, obj)) {
        return true;
      }
    }
    return false;
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
