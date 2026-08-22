## Finding: Missing curve-order bounds check on ECDSA signature components in consensus signature verification

`java-tron`'s core signature-weight verification path — `TransactionCapsule.checkWeight()`, used by every transaction broadcast, permission check, and block/transaction consensus validation — never validates that the `r`/`s` components of a submitted signature are within `[1, N-1]` (the secp256k1 curve order). This is the same bug class as the reported libsecp256k1 issue: signatures with overflowing/degenerate `r`/`s` are accepted where other implementations would reject them. [1](#0-0) 

This contrasts with the two places in the codebase that *do* perform the check before recovery: the `ECRecover` precompile and `SM2`/`ECKey` signature classes expose `validateComponents()`, which explicitly rejects `r`/`s` outside `[1, SECP256K1N-1]`: [2](#0-1) [3](#0-2) 

But `checkWeight` only checks the raw byte length (`sig.size() < 65`) and then goes straight to `SignUtils.signatureToAddress(...)`, bypassing `validateComponents()` entirely: [4](#0-3) 

This is documented (but not fixed) in `SignUtils`: [5](#0-4) 

The recovery routine itself only sanity-checks `r.signum() >= 0` / `s.signum() >= 0`, never `r < N` or `s < N`: [6](#0-5) 

and later computes `sig.r.modInverse(n)` / `sig.s.add(sig.r)` without bounds checks: [7](#0-6) 

If `r` is crafted to be a multiple of `N` (e.g., `r == N`), `BigInteger.modInverse` throws an unchecked `ArithmeticException` ("BigInteger not invertible"). `checkWeight`/`validateSignature` declare only checked exceptions (`SignatureException`, `PermissionException`, `SignatureFormatException`), so this `ArithmeticException` is not caught along that call chain: [8](#0-7) 

That path is reached both from `Manager.pushTransaction` (mempool admission) and `Manager.processTransaction` (block application during sync/consensus), which do not wrap `validateSignature` in a catch-all for `RuntimeException`: [9](#0-8) [10](#0-9) 

### Impact
An attacker can construct an arbitrarily malformed 65-byte multisig signature (e.g., `r = N`, or `r`/`s` offset by multiples of `N`) and attach it to a broadcast transaction. Depending on the entry point:
- Via `Wallet.broadcastTransaction`/RPC, a generic `catch (Exception e)` happens to absorb it, but this is incidental, not a designed safeguard.
- Via block-application (`Manager.processTransaction`, invoked while syncing/applying blocks during consensus), an uncaught `ArithmeticException` propagating out of transaction processing is not declared/handled by the method's checked-exception contract, risking disruption of block application on any node that processes a maliciously crafted transaction/block — a denial-of-service vector reachable from an ordinary broadcast transaction.
- Independent of the crash risk, accepting `r`/`s` outside `[1, N-1]` is itself non-conformant with standard ECDSA validation used elsewhere in the codebase (e.g., `P256Verify`, `ECRecover`), creating inconsistent security posture across signature-checking paths and enabling non-canonical signature forms to be treated as valid in the multisig weight-accounting logic.

### Recommendation
Call `validateComponents()` (or equivalent `r`/`s` range checks against the curve order) inside `TransactionCapsule.checkWeight()` before invoking `SignUtils.signatureToAddress`, mirroring what `PrecompiledContracts.ECRecover` already does, and ensure `ECKey.recoverPubBytesFromSignature`/`SM2.recoverPubBytesFromSignature` reject `r`/`s` that are `0` or `>= N` up front (rather than relying on `BigInteger.modInverse` to fail), converting any such rejection into a checked `SignatureFormatException` so it's handled uniformly by callers.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L233-256)
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
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L468-496)
```java
  public static boolean validateSignature(Transaction transaction,
      byte[] hash, AccountStore accountStore, DynamicPropertiesStore dynamicPropertiesStore)
      throws PermissionException, SignatureException, SignatureFormatException {
    Transaction.Contract contract = transaction.getRawData().getContractList().get(0);
    int permissionId = contract.getPermissionId();
    byte[] owner = getOwner(contract);
    AccountCapsule account = accountStore.get(owner);
    Permission permission = null;
    if (account == null) {
      if (permissionId == 0) {
        permission = AccountCapsule.getDefaultPermission(ByteString.copyFrom(owner));
      }
      if (permissionId == 2) {
        permission = AccountCapsule
            .createDefaultActivePermission(ByteString.copyFrom(owner), dynamicPropertiesStore);
      }
    } else {
      permission = account.getPermissionById(permissionId);
    }
    if (permission == null) {
      throw new PermissionException("permission isn't exit");
    }
    checkPermission(permissionId, permission, contract);
    long weight = checkWeight(permission, transaction.getSignatureList(), hash, null);
    if (weight >= permission.getThreshold()) {
      return true;
    }
    return false;
  }
```

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L517-523)
```java
  @Nullable
  public static byte[] recoverPubBytesFromSignature(int recId,
      ECDSASignature sig, byte[] messageHash) {
    check(recId >= 0, "recId must be positive");
    check(sig.r.signum() >= 0, "r must be positive");
    check(sig.s.signum() >= 0, "s must be positive");
    check(messageHash != null, "messageHash must not be null");
```

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L579-586)
```java
    BigInteger eInv = BigInteger.ZERO.subtract(e).mod(n);
    BigInteger rInv = sig.r.modInverse(n);
    BigInteger srInv = rInv.multiply(sig.s).mod(n);
    BigInteger eInvrInv = rInv.multiply(eInv).mod(n);
    ECPoint.Fp q = (ECPoint.Fp) ECAlgorithms.sumOfTwoMultiplies(CURVE
        .getG(), eInvrInv, R, srInv);
    return q.getEncoded(/* compressed */ false);
  }
```

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L923-946)
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


    public boolean validateComponents() {
      return validateComponents(r, s, v);
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L608-621)
```java
      try {
        System.arraycopy(data, 0, h, 0, 32);
        System.arraycopy(data, 32, v, 0, 32);
        System.arraycopy(data, 64, r, 0, 32);

        int sLength = data.length < 128 ? data.length - 96 : 32;
        System.arraycopy(data, 96, s, 0, sLength);

        SignatureInterface signature = SignUtils.fromComponents(r, s, v[31]
            , CommonParameter.getInstance().isECKeyCryptoEngine());
        if (validateV(v) && signature.validateComponents()) {
          out = new DataWord(SignUtils.signatureToAddress(h, signature
              , CommonParameter.getInstance().isECKeyCryptoEngine()));
        }
```

**File:** crypto/src/main/java/org/tron/common/crypto/SignUtils.java (L14-27)
```java
  /**
   * Strict signature-length check for admission entry-points (RPC broadcast,
   * P2P transaction ingress, peer hello handshake). Accepts only sizes in
   * [{@link org.tron.core.Constant#PER_SIGN_LENGTH PER_SIGN_LENGTH},
   * {@link org.tron.core.Constant#MAX_PER_SIGN_LENGTH MAX_PER_SIGN_LENGTH}].
   *
   * <p>Consensus paths (e.g. {@code TransactionCapsule.checkWeight}) intentionally
   * keep the looser {@code size < 65} check to remain compatible with historical
   * on-chain signatures that carry trailing padding bytes; do not call this
   * helper from those paths.
   */
  public static boolean isValidLength(int size) {
    return size >= PER_SIGN_LENGTH && size <= MAX_PER_SIGN_LENGTH;
  }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L904-909)
```java
    try {
      if (!trx.validateSignature(chainBaseManager.getAccountStore(),
          chainBaseManager.getDynamicPropertiesStore())) {
        throw new ValidateSignatureException(String.format("trans sig validate failed, id: %s",
            trx.getTransactionId()));
      }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1537-1546)
```java
    validateTapos(trxCap);
    validateCommon(trxCap);

    validateDup(trxCap);

    if (!trxCap.validateSignature(chainBaseManager.getAccountStore(),
        chainBaseManager.getDynamicPropertiesStore())) {
      throw new ValidateSignatureException(
          String.format(" %s transaction signature validate failed", txId));
    }
```
