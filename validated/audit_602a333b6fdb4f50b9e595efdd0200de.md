## Title
Single-step permission (ownership) update with no reachability/validation check in `AccountPermissionUpdateActuator` can permanently brick account control - (File: `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java`)

### Summary
`AccountPermissionUpdateContract` lets an account replace its `Owner`, `Witness`, and `Active` permissions in a single atomic step, with no "propose → accept" two-step confirmation and no check that the new `Owner` permission is still controllable by the caller. This is the same bug class as the reported `OwnableUpgradeable` single-step ownership transfer: a mistaken update (wrong address, unreachable threshold, or exclusion of the original owner key) irrecoverably transfers/locks account control in one transaction, with no ability to revert.

### Finding Description
The actuator's `execute()` directly overwrites the account's permission set as soon as `validate()` passes: it unpacks the `AccountPermissionUpdateContract`, then calls `account.updatePermissions(...)` and persists it — no staging, no pending state, no acknowledgment step from a "new owner." [1](#0-0) 

`validate()` and the helper `checkPermission()` only check structural constraints on the submitted `Permission` (key count bounds, non-zero weights, threshold ≤ weight sum, name length, parent id, valid addresses, and — for `Active` permissions — valid operation bitmap). They never verify that the *transaction signer/current owner* remains part of the new `Owner` permission, nor that the new permission set is reachable by any key the account previously controlled. [2](#0-1) [3](#0-2) 

`AccountCapsule.updatePermissions` performs an unconditional overwrite of `OwnerPermission`, clears and rebuilds `ActivePermission`, and conditionally sets `WitnessPermission` — there is no rollback/staging capsule and no record of the prior permission set for recovery. [4](#0-3) 

Because every future signature-weight check (`addSign`/`checkWeight`) is evaluated strictly against the *currently stored* permission, once a bad `Owner`/`Active` permission is committed with keys the account holder does not control (or with an unreachable threshold), no subsequent `AccountPermissionUpdateContract` (or any other contract requiring that permission) can ever be authorized again. [5](#0-4) 

### Impact Explanation
This is the direct functional analog of the `OwnableUpgradeable` single-step transfer issue: instead of a smart-contract `owner`, java-tron's per-account authority model (`Owner`/`Active`/`Witness` permissions) is transferred/rewritten atomically. A malformed update (e.g., typo'd key address, threshold unreachable by controlled keys, or omission of the caller's own key from the new `Owner` permission) permanently and irreversibly bricks the account: the account can no longer sign transactions requiring `Owner`/`Active` authority, cannot freeze/vote/transfer under multi-sig, and if the account `isWitness()` (a Super Representative), its `Witness` permission and consequently block-production/voting participation can be lost with no recovery path. This is high impact since it can permanently disable core account functionality including for witness accounts.

### Likelihood Explanation
Low — this requires a user/admin error when constructing the `AccountPermissionUpdateContract` (e.g., pasting a wrong address, misconfiguring thresholds/weights, or excluding themselves from the resulting permission), matching the reported issue's stated likelihood ("requires an error on the admin side").

### Recommendation
Introduce a two-step confirmation pattern for permission/ownership changes analogous to `Ownable2Step`: stage the proposed `Owner`/`Active`/`Witness` permissions in a "pending" state, and require a subsequent transaction — signed under the *new* permission set — to explicitly accept/activate it before the old permission is discarded. Additionally, `AccountPermissionUpdateActuator.validate()` could optionally warn/reject updates where the submitting key's weight is not present/reachable in the new `Owner` permission, reducing the chance of accidental self-lockout.

### Proof of Concept
1. Account `A` (optionally a witness) holds funds/voting rights under its default `Owner`/`Active` permission.
2. `A` submits `AccountPermissionUpdateContract` with `owner_address = A`, and a new `Owner` permission whose `keys` list contains only an address `B` that `A` does not control (typo, wrong copy-paste, or an address with no known private key), with `threshold` satisfiable by `B`'s weight.
3. `checkPermission()` passes all structural checks (key count > 0, distinct addresses, valid Base58/hex address format, weight sum ≥ threshold) — see [6](#0-5)  — because it never checks that the signer of this very transaction remains authorized under the new permission.
4. `execute()` commits the new permission via `updatePermissions()`, overwriting the old `Owner` permission irreversibly.
5. Any future transaction from `A` requiring `Owner`-level authority (including a corrective `AccountPermissionUpdateContract`) fails signature-weight validation in `TransactionCapsule.checkWeight`/`getWeight`, permanently bricking account control (and witness functionality if `A.getIsWitness()`).

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L43-52)
```java
    final AccountPermissionUpdateContract accountPermissionUpdateContract;
    try {
      accountPermissionUpdateContract = any.unpack(AccountPermissionUpdateContract.class);

      byte[] ownerAddress = accountPermissionUpdateContract.getOwnerAddress().toByteArray();
      AccountCapsule account = accountStore.get(ownerAddress);
      account.updatePermissions(accountPermissionUpdateContract.getOwner(),
          accountPermissionUpdateContract.getWitness(),
          accountPermissionUpdateContract.getActivesList());
      accountStore.put(ownerAddress, account);
```

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L71-93)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L95-122)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L208-228)
```java
    Permission owner = accountPermissionUpdateContract.getOwner();
    Permission witness = accountPermissionUpdateContract.getWitness();
    List<Permission> actives = accountPermissionUpdateContract.getActivesList();

    if (owner.getType() != PermissionType.Owner) {
      throw new ContractValidateException("owner permission type is error");
    }
    checkPermission(owner);
    if (accountCapsule.getIsWitness()) {
      if (witness.getType() != PermissionType.Witness) {
        throw new ContractValidateException("witness permission type is error");
      }
      checkPermission(witness);
    }
    for (Permission permission : actives) {
      if (permission.getType() != PermissionType.Active) {
        throw new ContractValidateException("active permission type is error");
      }
      checkPermission(permission);
    }
    return true;
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L1301-1320)
```java
  public void updatePermissions(Permission owner, Permission witness, List<Permission> actives) {
    Builder builder = this.account.toBuilder();

    owner = owner.toBuilder().setId(0).build();
    builder.setOwnerPermission(owner);
    if (witness != null && builder.getIsWitness()) {
      witness = witness.toBuilder().setId(1).build();
      builder.setWitnessPermission(witness);
    }

    builder.clearActivePermission();
    if (actives != null) {
      for (int i = 0; i < actives.size(); i++) {
        Permission permission = actives.get(i).toBuilder().setId(i + 2).build();
        builder.addActivePermission(permission);
      }
    }

    this.account = builder.build();
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L597-629)
```java
  public void addSign(byte[] privateKey, AccountStore accountStore)
      throws PermissionException, SignatureException, SignatureFormatException {
    Transaction.Contract contract = this.transaction.getRawData().getContract(0);
    int permissionId = contract.getPermissionId();
    byte[] owner = getOwnerAddress();
    AccountCapsule account = accountStore.get(owner);
    if (account == null) {
      throw new PermissionException("Account is not exist!");
    }
    Permission permission = account.getPermissionById(permissionId);
    if (permission == null) {
      throw new PermissionException("permission isn't exit");
    }
    checkPermission(permissionId, permission, contract);
    List<ByteString> approveList = new ArrayList<>();
    SignInterface cryptoEngine = SignUtils
        .fromPrivate(privateKey, CommonParameter.getInstance().isECKeyCryptoEngine());
    byte[] address = cryptoEngine.getAddress();
    if (this.transaction.getSignatureCount() > 0) {
      checkWeight(permission, this.transaction.getSignatureList(),
          this.getTransactionId().getBytes(),
          approveList);
      if (approveList.contains(ByteString.copyFrom(address))) {
        throw new PermissionException(encode58Check(address) + " had signed!");
      }
    }

    long weight = getWeight(permission, address);
    if (weight == 0) {
      throw new PermissionException(
          ByteArray.toHexString(privateKey) + "'s address is " + encode58Check(address)
              + " but it is not contained of permission.");
    }
```
