### Title
Authorization-status divergence between `getTransactionSignWeight`/`getTransactionApprovedList` and `validateSignature` for not-yet-existing accounts - ([File: chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java])

### Summary
`TransactionUtil.getTransactionSignWeight` and `Wallet.getTransactionApprovedList` unconditionally reject a transaction with `PermissionException("Account does not exist!")` whenever `AccountStore.get(owner)` returns `null`, regardless of `permissionId`. `TransactionCapsule.validateSignature` — the function actually invoked on the authoritative broadcast/consensus path — instead synthesizes a default `Permission` for `permissionId == 0` or `permissionId == 2` when the account does not exist, and evaluates the real signature weight against it. This means a transaction that the two "advisory" endpoints report as denied can in fact be accepted by `broadcastTransaction`/`processTransaction`.

### Finding Description
- `TransactionUtil.getTransactionSignWeight` throws immediately if the account is missing, before ever reaching `checkWeight`: [1](#0-0) 

- `Wallet.getTransactionApprovedList` has the identical unconditional check: [2](#0-1) 

- `TransactionCapsule.validateSignature`, used by `TransactionCapsule.validatePubSignature`/`validateSignature` and therefore by `Manager.processTransaction` (the actual consensus/broadcast validation path), synthesizes a default permission when the account is missing and `permissionId` is `0` or `2`, then proceeds to compute weight with `checkWeight` and returns `true`/`false` based on the threshold — it never throws for this case: [3](#0-2) 

- The broadcast path indeed calls this validator during transaction processing: [4](#0-3) 

All three functions are reachable by an unprivileged attacker: `/wallet/getsignweight` → `TransactionUtil.getTransactionSignWeight` [5](#0-4) , `/wallet/getapprovedlist` → `Wallet.getTransactionApprovedList` [6](#0-5) , and `/wallet/broadcasttransaction` (or gRPC `broadcastTransaction`) → `Manager.processTransaction` → `TransactionCapsule.validateSignature`.

Root cause: `getTransactionSignWeight`/`getTransactionApprovedList` never re-implement the "account doesn't exist but permissionId is 0/2" default-permission fallback that `validateSignature` implements, so the three checks are not derived from a single shared permission-resolution routine — a partial re-implementation drift.

### Impact Explanation
An attacker (or an honest relayer trusting the advisory endpoints) constructs a `TransferContract`/other single-contract transaction where `owner_address` is a fresh key that has never appeared in `AccountStore`, signs it correctly with that key (`permissionId` defaulted to `0`). Querying `/wallet/getsignweight` or `/wallet/getapprovedlist` for this transaction returns `PERMISSION_ERROR`/`OTHER_ERROR` with message `"Account does not exist!"`, falsely indicating the transaction cannot be authorized. However, submitting the identical transaction to `/wallet/broadcasttransaction` succeeds signature validation via `TransactionCapsule.validateSignature`'s default-permission fallback and can be accepted into the chain. This is exactly the "reverse" divergence described in scope: a wallet/relayer trusting the advisory endpoint's false "not enough permission"/error result would refuse to relay a transaction that the authoritative path would actually accept, or conversely could be confused about the true authorization state of a transaction it observes on-chain.

### Likelihood Explanation
Fully attacker-controlled and deterministic: no privileged state is required — only an owner address that has not yet been created in `AccountStore` (trivial to generate, since brand-new keys have no on-chain account until they first appear as a `to_address`/`owner_address` in a processed transaction). The divergence is 100% reproducible for `permissionId in {0, 2}` on any not-yet-existing account, and applies to any single-contract transaction type, including `TransferContract` used by `TransferServlet`.

### Recommendation
Factor permission resolution (including the account-missing default-permission fallback for `permissionId == 0`/`2`) into one shared helper used by `getTransactionSignWeight`, `getTransactionApprovedList`, and `validateSignature`, so all three agree on how to resolve `Permission` for a given `(owner, permissionId)` pair before computing weight. At minimum, update `getTransactionSignWeight` and `getTransactionApprovedList` to apply the same `AccountCapsule.getDefaultPermission`/`createDefaultActivePermission` fallback that `TransactionCapsule.validateSignature` uses when `account == null`.

### Proof of Concept
```java
@Test
public void testDivergentAuthorizationForMissingAccount() throws Exception {
  ECKey ecKey = new ECKey(Utils.getRandom());
  byte[] owner = ecKey.getAddress();
  // Intentionally do NOT put an account into accountStore.

  Transaction unsigned = Transaction.newBuilder().setRawData(
      Transaction.raw.newBuilder().addContract(
          Contract.newBuilder().setType(ContractType.TransferContract)
              .setParameter(Any.pack(TransferContract.newBuilder()
                  .setAmount(1)
                  .setOwnerAddress(ByteString.copyFrom(owner))
                  .setToAddress(ByteString.copyFrom(ByteArray.fromHexString(RECEIVER_ADDRESS)))
                  .build())).build()).build()).build();

  TransactionCapsule capsule = new TransactionCapsule(unsigned);
  capsule.sign(ecKey.getPrivKeyBytes());
  Transaction signed = capsule.getInstance();

  // 1) advisory endpoint: getTransactionSignWeight -> reports PERMISSION_ERROR
  TransactionSignWeight tsw = transactionUtil.getTransactionSignWeight(signed);
  assertEquals(TransactionSignWeight.Result.response_code.PERMISSION_ERROR,
      tsw.getResult().getCode());
  assertTrue(tsw.getResult().getMessage().contains("Account does not exist!"));

  // 2) advisory endpoint: getTransactionApprovedList -> also reports error
  TransactionApprovedList tal = wallet.getTransactionApprovedList(signed);
  assertNotEquals(TransactionApprovedList.Result.response_code.SUCCESS,
      tal.getResult().getCode());
  assertTrue(tal.getResult().getMessage().contains("Account does not exist!"));

  // 3) authoritative path: TransactionCapsule.validateSignature succeeds
  byte[] hash = capsule.getTransactionId().getBytes();
  boolean valid = TransactionCapsule.validateSignature(
      signed, hash, chainBaseManager.getAccountStore(),
      chainBaseManager.getDynamicPropertiesStore());
  assertTrue(valid); // divergence: broadcast path accepts what advisory endpoints reject
}
```
Expected assertions confirm: `getTransactionSignWeight`/`getTransactionApprovedList` return an error state for a missing account, while `TransactionCapsule.validateSignature` (the function gating `Manager.processTransaction`/`broadcastTransaction`) returns `true` for the same transaction, demonstrating the authorization-decision divergence.

### Citations

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L226-235)
```java
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
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L659-669)
```java
        Contract contract = trx.getRawData().getContract(0);
        byte[] owner = TransactionCapsule.getOwner(contract);
        AccountCapsule account = chainBaseManager.getAccountStore().get(owner);
        if (account == null) {
          throw new PermissionException("Account does not exist!");
        }
        int permissionId = contract.getPermissionId();
        Permission permission = account.getPermissionById(permissionId);
        if (permission == null) {
          throw new PermissionException("Permission for this, does not exist!");
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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1542-1546)
```java
    if (!trxCap.validateSignature(chainBaseManager.getAccountStore(),
        chainBaseManager.getDynamicPropertiesStore())) {
      throw new ValidateSignatureException(
          String.format(" %s transaction signature validate failed", txId));
    }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetTransactionSignWeightServlet.java (L24-37)
```java
  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      Transaction transaction = Util.packTransaction(params.getParams(), params.isVisible());
      TransactionSignWeight reply = transactionUtil.getTransactionSignWeight(transaction);
      if (reply != null) {
        response.getWriter().println(Util.printTransactionSignWeight(reply, params.isVisible()));
      } else {
        response.getWriter().println("{}");
      }
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetTransactionApprovedListServlet.java (L24-37)
```java
  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      Transaction transaction = Util.packTransaction(params.getParams(), params.isVisible());
      TransactionApprovedList reply = wallet.getTransactionApprovedList(transaction);
      if (reply != null) {
        response.getWriter().println(Util.printTransactionApprovedList(reply, params.isVisible()));
      } else {
        response.getWriter().println("{}");
      }
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```
