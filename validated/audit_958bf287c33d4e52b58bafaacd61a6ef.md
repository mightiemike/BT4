### Title
Signature Malleability Enables Multi-Sig Weight Double-Counting in `ValidateMultiSign` TVM Precompile - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
The `ValidateMultiSign` precompiled contract, reachable by any smart contract via a `STATICCALL`/`CALL` from an anonymous, unprivileged broadcast transaction, tallies signer weight for an account `Permission` and accepts signatures whose `s` component is not constrained to the canonical low-half range. Combined with a de-duplication check that keys on the raw signature bytes (not solely the recovered signer address), an attacker holding a single private key can submit that key's signature twice — once in its original form and once in its ECDSA-malleable form (`s' = N - s`, flipped recovery id) — and have the same key's weight counted twice toward the `Permission` threshold.

### Finding Description
The recovery/validation primitive lives in `ECKey.ECDSASignature.validateComponents()`: [1](#0-0) 
This checks `v ∈ {27,28}` and `1 <= r,s < SECP256K1N`, but it never restricts `s` to the canonical low half (`s <= N/2`). Consequently, for any signature `(r, s, v)` that recovers signer `A`, the malleable variant `(r, N-s, v')` also passes `validateComponents()` and recovers the *same* signer `A` — this is exactly the "s-value malleability" issue flagged in the external report.

`PrecompiledContracts.recoverAddrBySign()` uses this same unrestricted validation path: [2](#0-1) 

The `ValidateMultiSign` precompile (reachable at its fixed precompiled address from any TVM contract call triggered by an ordinary, unprivileged transaction) uses this recovery function to sum signer weight: [3](#0-2) 

The critical flaw is the de-duplication logic:
```java
byte[] recoveredAddr = recoverAddrBySign(sign, hash);
sign = merge(recoveredAddr, sign);
if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
  if (ByteArray.matrixContains(executedSignList, sign)) {
    continue;   // only skips if the EXACT signature bytes were seen before
  }
  MUtil.checkCPUTime();
}
long weight = TransactionCapsule.getWeight(permission, recoveredAddr);
...
totalWeight += weight;
executedSignList.add(sign);
executedSignList.add(recoveredAddr);
```
The inner `continue` only fires when the *literal signature bytes* were already processed. If the same address is recovered again from a *different-but-malleable* signature byte string, the code does not skip it — it proceeds to add that key's weight a second time.

By contrast, the account-level, transaction-signing path in `TransactionCapsule.checkWeight()` (used to authorize real on-chain transactions) already recognized and mitigated this exact class of bug: post-fork `VERSION_4_7_1` it dedupes strictly by the *recovered address* (`encode58Check(address)`), not by raw signature bytes: [4](#0-3) 
That fix was never carried over to the `ValidateMultiSign` (and, by extension, `BatchValidateSign`) TVM precompiles, which continue to key their de-duplication on the raw signature bytes appended to the address.

### Impact Explanation
`ValidateMultiSign` is designed to let smart contracts (e.g., on-chain multisig wallets, escrow, DAO-style contracts) verify that an account's `Permission` threshold (a weighted N-of-M signer scheme) has been satisfied before it authorizes an action, purely from data supplied in the call — it does not require the caller to be privileged or the contract owner. Because a single key's weight can be counted twice (or more, with additional malleable variants derived from the same `(r,s)` pair via further recovery-id/s flips within validity bounds), any contract that relies on this precompile for authorization can be tricked into believing a higher-weight/threshold multisig approval occurred when in fact only one private key participated. This can lead to unauthorized execution of privileged contract logic (e.g., unauthorized withdrawal/approval in a multisig wallet contract), i.e. unauthorized account/asset operation — a concrete, protocol-level authorization bypass reachable from ordinary contract calls.

### Likelihood Explanation
High. No privileged position is required — an attacker only needs one key that is a member of the target account's `Permission` (even with the smallest configured weight) and knowledge of ECDSA math to compute the malleable `s' = N - s` (and corresponding recovery id) for a signature they already control. The call path is a normal `TriggerSmartContract`/precompile invocation, not gated by any special permission check, and the affected code (`ValidateMultiSign`) is present in `PrecompiledContracts.java` without any canonical-`s` enforcement.

### Recommendation
1. Enforce canonical low-`s` signatures in `ECKey.ECDSASignature.validateComponents()` (and the SM2 equivalent) by rejecting `s > SECP256K1N/2`, consistent with EIP-2/OpenZeppelin `ECDSA.sol` guidance cited in the external report.
2. Independent of (1), fix `ValidateMultiSign` (and audit `BatchValidateSign`) to de-duplicate signer weight strictly by the recovered address, mirroring the fix already applied in `TransactionCapsule.checkWeight()`, so that no address can contribute weight more than once regardless of how many distinct byte-level signature encodings are supplied for it.

### Proof of Concept
1. Create account `A` with an active `Permission` requiring `threshold = 2`, containing key `K` with `weight = 2` (or two entries mapping to keys, at least one controlled by attacker with weight ≥ threshold when doubled).
2. Compute `hash = sha256(address || permissionId || data)` per `ValidateMultiSign.execute`.
3. Sign `hash` with `K` to get `sig1 = (r, s, v)`.
4. Derive the malleable variant `sig2 = (r, N-s, v')` (still passes `validateComponents()`, recovers the same address as `sig1`).
5. Call the `ValidateMultiSign` precompile with `signatures = [sig1, sig2]` for account `A`/`permissionId`.
6. Observe `recoverAddrBySign` returns the same address for both entries, but because `merge(recoveredAddr, sig)` differs between `sig1` and `sig2`, the `continue` skip never triggers; `totalWeight` accumulates `K`'s weight twice, exceeding `permission.getThreshold()` and returning `DataWord.ONE()` (approved) despite only one distinct signer key having participated.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1080-1119)
```java
      AccountCapsule account = this.getDeposit().getAccount(address);
      if (account != null) {
        try {
          Permission permission = account.getPermissionById(permissionId);
          if (permission != null) {
            //calculate weight
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

            if (totalWeight >= permission.getThreshold()) {
              return Pair.of(true, dataOne());
            }
          }
        } catch (Throwable t) {
          if (t instanceof OutOfTimeException) {
            throw t;
          }
          logger.info("ValidateMultiSign error:{}", t.getMessage());
        }
      }
      return Pair.of(true, DATA_FALSE);
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L233-270)
```java
  public static long checkWeight(Permission permission, List<ByteString> sigs, byte[] hash,
      List<ByteString> approveList)
      throws SignatureException, PermissionException, SignatureFormatException {
    long currentWeight = 0;
    if (sigs.size() > permission.getKeysCount()) {
      throw new PermissionException(
          "Signature count is " + (sigs.size()) + " more than key counts of permission : "
              + permission.getKeysCount());
    }
    HashMap addMap = new HashMap();
    for (ByteString sig : sigs) {
      if (sig.size() < 65) {
        throw new SignatureFormatException(
            "Signature size is " + sig.size());
      }
      String base64 = TransactionCapsule.getBase64FromByteString(sig);
      byte[] address = SignUtils
          .signatureToAddress(hash, base64, CommonParameter.getInstance().isECKeyCryptoEngine());
      long weight = getWeight(permission, address);
      if (weight == 0) {
        throw new PermissionException(
            ByteArray.toHexString(hash) + " is signed by " + encode58Check(address)
                + " but it is not contained of permission.");
      }
      if (ForkController.instance().pass(Parameter.ForkBlockVersionEnum.VERSION_4_7_1)) {
        base64 = encode58Check(address);
      }
      if (addMap.containsKey(base64)) {
        throw new PermissionException(encode58Check(address) + " has signed twice!");
      }
      addMap.put(base64, weight);
      if (approveList != null) {
        approveList.add(ByteString.copyFrom(address)); //out put approve list.
      }
      currentWeight += weight;
    }
    return currentWeight;
  }
```
