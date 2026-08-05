### Title
Divergence between `TransactionUtil.getTransactionSignWeight` (public estimate API) and the actual consensus signature-weight check causes public permission-weight estimates to disagree with real transaction outcomes - ([File: actuator/src/main/java/org/tron/core/utils/TransactionUtil.java])

### Finding Description
`TransactionUtil.getTransactionSignWeight` — the handler backing the public `GetTransactionSignWeight` RPC/HTTP API — silently rewrites the transaction's signatures via `truncateSignatures` before computing the weight: [1](#0-0) 

```java
public static Transaction truncateSignatures(Transaction trx) {
  Transaction.Builder builder = trx.toBuilder().clearSignature();
  for (ByteString sig : trx.getSignatureList()) {
    if (sig.size() > PER_SIGN_LENGTH) {
      builder.addSignature(ByteString.copyFrom(sig.substring(0, PER_SIGN_LENGTH).toByteArray()));
    } else {
      builder.addSignature(sig);
    }
  }
  return builder.build();
}
```

This truncated transaction is then fed into `TransactionCapsule.checkWeight` to compute the reported `TransactionSignWeight` (`ENOUGH_PERMISSION` / `NOT_ENOUGH_PERMISSION`): [2](#0-1) 

The real, consensus-path signature validation (invoked from `Manager`/`TransactionCapsule` during actual block application) does **not** go through `truncateSignatures` at all — it operates on the raw, attacker-supplied signature bytes. This asymmetry is explicitly documented in `SignUtils.isValidLength`: [3](#0-2) 

```java
/**
 * Strict signature-length check for admission entry-points (RPC broadcast,
 * P2P transaction ingress, peer hello handshake). Accepts only sizes in
 * [PER_SIGN_LENGTH, MAX_PER_SIGN_LENGTH].
 *
 * Consensus paths (e.g. TransactionCapsule.checkWeight) intentionally
 * keep the looser {@code size < 65} check to remain compatible with historical
 * on-chain signatures that carry trailing padding bytes; do not call this
 * helper from those paths.
 */
public static boolean isValidLength(int size) { ... }
```

So there are (at least) three distinct signature-length semantics in the codebase:
1. Broadcast/P2P admission (`isValidLength`) — strict `[PER_SIGN_LENGTH, MAX_PER_SIGN_LENGTH]`.
2. `TransactionCapsule.checkWeight` (actual consensus weight computation, used both during block application and — without truncation — supposedly on the real path) — looser `size < 65` check on the *raw* bytes.
3. `TransactionUtil.getTransactionSignWeight` (public estimate API) — truncates any signature `> PER_SIGN_LENGTH` down to exactly `PER_SIGN_LENGTH` bytes *before* calling `checkWeight`.

Because path 3 mutates the byte content passed into the same `checkWeight` function that path 2 uses on raw bytes, an attacker can craft a signature whose first `PER_SIGN_LENGTH` bytes recover a *valid, authorized* address once truncated (and thus pass `checkWeight` in the estimate path, yielding `ENOUGH_PERMISSION`), while the untruncated raw bytes recover a different address or fail the `size < 65` gate on the real path, or vice versa (an oversized signature that fails admission on the real broadcast path — because it violates `isValidLength`'s `MAX_PER_SIGN_LENGTH` bound — can still be silently truncated and evaluated as valid by the estimate API). The two code paths that are supposed to answer the same question ("does this transaction have enough permission weight?") therefore consume different byte sequences and can produce different `Result` codes for the identical submitted `Transaction` object.

### Impact Explanation
A caller of the public `GetTransactionSignWeight` API (used by wallets/dApps to decide whether enough co-signers have signed a multi-sig transaction before broadcasting) can receive `ENOUGH_PERMISSION` for a transaction that will actually be rejected (or, worse, silently pass) at the real validation/consensus stage, since that stage evaluates the untruncated bytes. This is an estimate/actual divergence bug rather than a direct fund-theft primitive: it can cause user-facing tooling to make incorrect broadcast decisions, and in adversarial multi-sig setups could be leveraged to get a co-signer to believe a transaction is fully authorized (via the estimate API) when the real, on-chain outcome differs.

### Likelihood Explanation
The trigger is trivial for any unprivileged actor: submit any raw `Transaction` (never even needs to be broadcast) to the public `GetTransactionSignWeight` API with one signature entry longer than `PER_SIGN_LENGTH` bytes. No special privileges, keys, or node access are required — this is a pure client-supplied-input divergence between two API surfaces that are documented (in the `SignUtils` comment) as intentionally using different signature-length semantics. The precondition is simply constructing a signature byte string with extra trailing bytes, which is fully attacker-controlled.

### Recommendation
Make `TransactionUtil.getTransactionSignWeight` compute weight against exactly the same signature bytes and the same length-validation rule that the real consensus/validation path uses — i.e., remove the truncation step (or apply the identical `checkWeight`/`isValidLength` semantics used on the broadcast/consensus path) so that the estimate path can never diverge from the actual on-chain evaluation for the same `Transaction`.

### Proof of Concept
```java
// Integration-style test sketch
@Test
public void estimateVsActualDivergesForOversizedSignature() {
  Transaction trx = buildSingleContractTransaction(ownerAddress);
  byte[] rawHash = Sha256Hash.hash(true, trx.getRawData().toByteArray());

  // Build a real ECDSA signature (65 bytes) for a co-signer, then pad it
  byte[] validSig = ECKey.fromPrivate(coSignerKey).sign(rawHash).toByteArray(); // 65 bytes
  byte[] oversizedSig = Arrays.copyOf(validSig, validSig.length + 5); // now > PER_SIGN_LENGTH

  Transaction trxWithOversizedSig = trx.toBuilder()
      .addSignature(ByteString.copyFrom(oversizedSig))
      .build();

  // Estimate path: truncates back to 65 bytes -> recovers valid co-signer address
  TransactionSignWeight estimate =
      transactionUtil.getTransactionSignWeight(trxWithOversizedSig);
  // Expect estimate.getResult().getCode() == Result.response_code.ENOUGH_PERMISSION

  // Actual path: pass raw (untruncated) trxWithOversizedSig into the real validation
  // used at block-application time (e.g., TransactionCapsule.checkWeight directly, or
  // via manager.pushTransaction(new TransactionCapsule(trxWithOversizedSig)))
  boolean actualAccepted;
  try {
    long weight = TransactionCapsule.checkWeight(permission,
        trxWithOversizedSig.getSignatureList(), rawHash, new ArrayList<>());
    actualAccepted = weight >= permission.getThreshold();
  } catch (SignatureException | PermissionException e) {
    actualAccepted = false;
  }

  // Assert divergence: estimate says ENOUGH_PERMISSION but actual validation fails
  assertEquals(Result.response_code.ENOUGH_PERMISSION, estimate.getResult().getCode());
  assertFalse(actualAccepted);
}
```

This demonstrates that `TransactionUtil.getTransactionSignWeight` and the real signature-weight validation used during actual transaction processing can produce contradictory results for the same `Transaction` object when a signature exceeds `PER_SIGN_LENGTH`.

### Citations

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L187-197)
```java
  public static Transaction truncateSignatures(Transaction trx) {
    Transaction.Builder builder = trx.toBuilder().clearSignature();
    for (ByteString sig : trx.getSignatureList()) {
      if (sig.size() > PER_SIGN_LENGTH) {
        builder.addSignature(ByteString.copyFrom(sig.substring(0, PER_SIGN_LENGTH).toByteArray()));
      } else {
        builder.addSignature(sig);
      }
    }
    return builder.build();
  }
```

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L199-258)
```java
  public TransactionSignWeight getTransactionSignWeight(Transaction trx) {
    TransactionSignWeight.Builder tswBuilder = TransactionSignWeight.newBuilder();
    Result.Builder resultBuilder = Result.newBuilder();
    if (trx.getSignatureCount() > chainBaseManager.getDynamicPropertiesStore()
        .getTotalSignNum()) {
      resultBuilder.setCode(Result.response_code.OTHER_ERROR);
      resultBuilder.setMessage("too many signatures");
      tswBuilder.setResult(resultBuilder);
      return tswBuilder.build();
    }

    trx = truncateSignatures(trx);
    TransactionExtention.Builder trxExBuilder = TransactionExtention.newBuilder();
    trxExBuilder.setTransaction(trx);
    trxExBuilder.setTxid(ByteString.copyFrom(Sha256Hash.hash(CommonParameter
        .getInstance().isECKeyCryptoEngine(), trx.getRawData().toByteArray())));
    Return.Builder retBuilder = Return.newBuilder();
    retBuilder.setResult(true).setCode(response_code.SUCCESS);
    trxExBuilder.setResult(retBuilder);
    tswBuilder.setTransaction(trxExBuilder);

    if (trx.getRawData().getContractCount() == 0) {
      resultBuilder.setCode(Result.response_code.OTHER_ERROR);
      resultBuilder.setMessage("Invalid transaction: no valid contract");
    } else {
      try {
        Contract contract = trx.getRawData().getContract(0);
        byte[] owner = TransactionCapsule.getOwner(contract);
        AccountCapsule account = chainBaseManager.getAccountStore().get(owner);
        if (Objects.isNull(account)) {
          throw new PermissionException("Account does not exist!");
        }
        int permissionId = contract.getPermissionId();
        Permission permission = account.getPermissionById(permissionId);
        if (permission == null) {
          throw new PermissionException("Permission for this, does not exist!");
        }
        if (permissionId != 0) {
          if (permission.getType() != PermissionType.Active) {
            throw new PermissionException("Permission type is wrong!");
          }
          //check operations
          if (!checkPermissionOperations(permission, contract)) {
            throw new PermissionException("Permission denied!");
          }
        }
        tswBuilder.setPermission(permission);
        if (trx.getSignatureCount() > 0) {
          List<ByteString> approveList = new ArrayList<>();
          long currentWeight = TransactionCapsule.checkWeight(permission, trx.getSignatureList(),
              Sha256Hash.hash(CommonParameter.getInstance()
                  .isECKeyCryptoEngine(), trx.getRawData().toByteArray()), approveList);
          tswBuilder.addAllApprovedList(approveList);
          tswBuilder.setCurrentWeight(currentWeight);
        }
        if (tswBuilder.getCurrentWeight() >= permission.getThreshold()) {
          resultBuilder.setCode(Result.response_code.ENOUGH_PERMISSION);
        } else {
          resultBuilder.setCode(Result.response_code.NOT_ENOUGH_PERMISSION);
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
