### Title
Multi-sig transactions are validated against the *current* Permission content, not the content that existed when co-signers signed — allowing threshold/keys to be changed underneath a pending transaction ([File: chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java])

### Summary
This is the closest reachable analog to the reported issue: a signed artifact (`Transaction`) references a *config identifier* (`permissionId`) rather than the actual configuration content (the `Permission` struct — its threshold, keys, weights, operations bitmap). The signature hash is computed only over `Transaction.raw` (owner address, `permissionId`, contract payload), never over the `Permission` itself. Because `AccountPermissionUpdateContract` can change the content addressed by a given `permissionId` at any time, previously collected signatures for a not-yet-broadcast multi-sig transaction remain "valid" against a permission set that has since changed — exactly the "hash doesn't depend on the config state" bug class described in the report (there, `DAppControl` address ≠ behavior; here, `permissionId` ≠ actual permission content).

### Finding Description
- Signature verification (`TransactionCapsule.validateSignature` / `checkWeight`) computes the message hash from `transaction.getRawData().toByteArray()` (i.e., `getTransactionId()`), which contains `Transaction.Contract.permissionId` — an integer index — but never the `Permission` (threshold, key list, weights, `operations` bitmap) that the index resolves to. [1](#0-0) 
- At verification time (which can happen well after signatures were originally collected, since a multi-sig `Transaction` is typically built and signed incrementally off-chain before being broadcast with `broadcastTransaction`/`getTransactionApprovedList`), the code looks up the **live** `Permission` object from the `AccountStore` by `permissionId` and evaluates `checkWeight` against it: [2](#0-1) [3](#0-2) 
- `AccountPermissionUpdateActuator.execute` freely rewrites the `Permission` for a given owner/active/witness `permissionId` (`account.updatePermissions(...)`), without any linkage to, or invalidation of, transactions that were already partially or fully signed against the old permission content: [4](#0-3) 
- Nothing in the signed payload binds the signature to a specific version/hash of the `Permission` struct — no permission version, no content hash, no "as of" nonce is included in `Transaction.raw` before hashing. The `Permission` proto itself carries only `type`, `id`, `permission_name`, `threshold`, `parent_id`, `operations`, `keys` — none of which is echoed into the signed transaction hash.

### Impact Explanation
This produces the same class of "hash doesn't depend on config state" risk as the report:
- A co-signer signs a transaction under active `permissionId=2` believing it requires, e.g., 3 keys at threshold 3 (`AccountPermissionUpdateActuator` allows any owner-quorum update to threshold/keys/operations for that same id, see `checkPermission`) [5](#0-4) .
- If the permission for that `permissionId` is subsequently updated (threshold lowered, a key removed/added, or the `operations` bitmap changed to permit different contract types) before the pending, partially-collected transaction is broadcast, the transaction's existing signatures are checked by `checkWeight` against the *new* `Permission`, not the one the signer(s) actually agreed to. This can let a transaction execute:
  - with fewer valid co-signers than originally required (threshold lowered), or
  - be authorized by a key that had no authority when the signer(s) originally signed (a newly added key/weight), or
  - be permitted for a broader set of contract types than the operations bitmap in effect when it was signed.
- Because the actuator's own `validate()`/`execute()` require satisfying the *Owner* permission threshold to change `Active`/`Witness` permissions, this is not exploitable by a fully unprivileged outsider with zero keys; it requires reachability through an account's own multi-party (owner-quorum) governance. However, it is a genuine cross-permission trust-assumption break for any of the account's *active*-permission co-signers, who have no cryptographic guarantee that the permission set they signed against is the one enforced at broadcast time — a legitimate, reachable, unprivileged-relative-to-the-active-signer-role divergence/authorization issue in the accounting/authorization path, directly analogous to the reported `AtlasVerification` finding.

### Likelihood Explanation
Requires: (1) an account using TRON's multi-sig/permission feature (`AllowMultiSign`), (2) a transaction being incrementally signed by active-permission holders before broadcast (a supported, documented workflow via `getTransactionSignWeight`/`addSign`/`getTransactionApprovedList`), and (3) an `AccountPermissionUpdateContract` executed by the owner-permission quorum in the intervening window. This is a realistic multi-party operational sequence (race between signature collection and permission update) rather than a purely theoretical scenario, and the code paths for both are exercised in production (`TransactionCapsule.checkWeight`, `AccountPermissionUpdateActuator.execute`).

### Recommendation
Bind the signed transaction to the exact permission content in effect when it was authorized, e.g., by embedding a permission "version"/content hash (or a monotonically increasing `permission_id` generation counter) into `Transaction.raw` and requiring `checkWeight`/`validateSignature` to reject if the stored `Permission`'s version does not match the one referenced by the transaction, analogous to including the expected `CallConfig` in the report's recommendation.

### Proof of Concept
Conceptual sequence (not independently executed against a live node in this review):
1. Account `A` has Active permission id=2, threshold=3, keys={K1,K2,K3} each weight 1.
2. Co-signers using K1 and K2 sign transaction `T` (weight=2 < threshold=3 → not yet broadcastable).
3. Before `T` is broadcast, the Owner-permission quorum submits `AccountPermissionUpdateContract` lowering active id=2 threshold to 2 (or adding attacker key K4 with high weight) via `AccountPermissionUpdateActuator.execute` [6](#0-5) .
4. `T` (still only signed by K1, K2, unchanged since its hash never encoded the old permission content) is broadcast; `TransactionCapsule.validateSignature` → `checkWeight` now evaluates the two old signatures against the *new* permission (threshold 2) [7](#0-6)  and it now satisfies the threshold and executes — despite the original signers never agreeing to a 2-of-N policy.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L463-496)
```java
  public static String getBase64FromByteString(ByteString sign) {
    Rsv rsv = Rsv.fromSignature(sign.toByteArray());
    return ECDSASignature.fromComponents(rsv.getR(), rsv.getS(), rsv.getV()).toBase64();
  }

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

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L647-680)
```java
  /**
   * validate signature
   */
  public boolean validatePubSignature(AccountStore accountStore,
      DynamicPropertiesStore dynamicPropertiesStore)
      throws ValidateSignatureException {
    if (!isVerified) {
      if (this.transaction.getSignatureCount() <= 0
              || this.transaction.getRawData().getContractCount() <= 0) {
        throw new ValidateSignatureException("miss sig or contract");
      }
      if (this.transaction.getSignatureCount() > dynamicPropertiesStore
              .getTotalSignNum()) {
        throw new ValidateSignatureException("too many signatures");
      }

      byte[] hash = getTransactionId().getBytes();

      long startNs = System.nanoTime();
      try {
        if (!validateSignature(this.transaction, hash, accountStore, dynamicPropertiesStore)) {
          isVerified = false;
          throw new ValidateSignatureException("sig error");
        }
      } catch (SignatureException | PermissionException | SignatureFormatException e) {
        isVerified = false;
        throw new ValidateSignatureException(e.getMessage());
      } finally {
        logSlowSigVerify(startNs);
      }
      isVerified = true;
    }
    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L34-69)
```java
  @Override
  public boolean execute(Object object) throws ContractExeException {
    TransactionResultCapsule result = (TransactionResultCapsule) object;
    if (Objects.isNull(result)) {
      throw new RuntimeException(ActuatorConstant.TX_RESULT_NULL);
    }

    AccountStore accountStore = chainBaseManager.getAccountStore();
    long fee = calcFee();
    final AccountPermissionUpdateContract accountPermissionUpdateContract;
    try {
      accountPermissionUpdateContract = any.unpack(AccountPermissionUpdateContract.class);

      byte[] ownerAddress = accountPermissionUpdateContract.getOwnerAddress().toByteArray();
      AccountCapsule account = accountStore.get(ownerAddress);
      account.updatePermissions(accountPermissionUpdateContract.getOwner(),
          accountPermissionUpdateContract.getWitness(),
          accountPermissionUpdateContract.getActivesList());
      accountStore.put(ownerAddress, account);

      adjustBalance(accountStore, ownerAddress, -fee);
      if (chainBaseManager.getDynamicPropertiesStore().supportBlackHoleOptimization()) {
        chainBaseManager.getDynamicPropertiesStore().burnTrx(fee);
      } else {
        adjustBalance(accountStore, accountStore.getBlackhole(), fee);
      }

      result.setStatus(fee, code.SUCESS);
    } catch (BalanceInsufficientException | InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      result.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    }

    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L71-146)
```java
  private boolean checkPermission(Permission permission) throws ContractValidateException {
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    if (permission.getKeysCount() > dynamicStore.getTotalSignNum()) {
      throw new ContractValidateException("number of keys in permission should not be greater "
          + "than " + dynamicStore.getTotalSignNum());
    }
    if (permission.getKeysCount() == 0) {
      throw new ContractValidateException("key's count should be greater than 0");
    }
    if (permission.getType() == PermissionType.Witness && permission.getKeysCount() != 1) {
      throw new ContractValidateException("Witness permission's key count should be 1");
    }
    if (permission.getThreshold() <= 0) {
      throw new ContractValidateException("permission's threshold should be greater than 0");
    }
    String name = permission.getPermissionName();
    if (!StringUtils.isEmpty(name) && name.length() > 32) {
      throw new ContractValidateException("permission's name is too long");
    }
    //check owner name ?
    if (permission.getParentId() != 0) {
      throw new ContractValidateException("permission's parent should be owner");
    }

    long weightSum = 0;
    List<ByteString> addressList = permission.getKeysList()
        .stream()
        .map(x -> x.getAddress())
        .distinct()
        .collect(toList());
    if (addressList.size() != permission.getKeysList().size()) {
      throw new ContractValidateException(
          "address should be distinct in permission " + permission.getType());
    }
    for (Key key : permission.getKeysList()) {
      if (!DecodeUtil.addressValid(key.getAddress().toByteArray())) {
        throw new ContractValidateException("key is not a validate address");
      }
      if (key.getWeight() <= 0) {
        throw new ContractValidateException("key's weight should be greater than 0");
      }
      try {
        weightSum = addExact(weightSum, key.getWeight());
      } catch (ArithmeticException e) {
        throw new ContractValidateException(e.getMessage());
      }
    }
    if (weightSum < permission.getThreshold()) {
      throw new ContractValidateException(
          "sum of all key's weight should not be less than threshold in permission " + permission
              .getType());
    }

    ByteString operations = permission.getOperations();
    if (permission.getType() != PermissionType.Active) {
      if (!operations.isEmpty()) {
        throw new ContractValidateException(
            permission.getType() + " permission needn't operations");
      }
      return true;
    }
    //check operations
    if (operations.isEmpty() || operations.size() != 32) {
      throw new ContractValidateException("operations size must 32");
    }

    byte[] types1 = dynamicStore.getAvailableContractType();
    for (int i = 0; i < 256; i++) {
      boolean b = (operations.byteAt(i / 8) & (1 << (i % 8))) != 0;
      boolean t = ((types1[(i / 8)] & 0xff) & (1 << (i % 8))) != 0;
      if (b && !t) {
        throw new ContractValidateException(i + " isn't a validate ContractType");
      }
    }
    return true;
  }
```
