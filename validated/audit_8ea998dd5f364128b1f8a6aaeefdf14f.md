### Title
Single-step, unrecoverable Owner Permission overwrite in `AccountPermissionUpdateActuator` can permanently lock an account - (File: `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java`)

### Summary
`AccountPermissionUpdateContract` lets any account holder replace its `Owner`, `Witness`, and `Active` permissions in a single, atomic transaction with no two-step confirmation and no requirement that the previously-authorized owner key remain part of the new permission set. This is the exact bug class described in the external report: a critical, security-relevant address/authority change performed as a single irreversible step, with no `pendingOwner`/claim mechanism to recover from a mistaken or unreachable address.

### Finding Description
`AccountPermissionUpdateActuator.execute()` directly overwrites the account's owner/witness/active permissions from the submitted contract fields and commits them to the `AccountStore` in one step: [1](#0-0) 

The underlying capsule mutation, `AccountCapsule.updatePermissions()`, unconditionally replaces the stored `ownerPermission` (and witness/active permissions) with whatever was supplied in the transaction: [2](#0-1) 

`validate()` and the helper `checkPermission()` only perform syntactic/structural checks — address format validity (`DecodeUtil.addressValid`), non-zero key weight, key uniqueness, threshold vs. weight-sum, permission name length, and (for `Active` permissions) the 32-byte operations bitmap — but never verify that:
- the resulting `Owner` permission still includes a key the current signer(s) actually control, or
- the new key(s) correspond to an address whose private key is known/reachable. [3](#0-2) [4](#0-3) 

Because `AccountPermissionUpdateContract` itself requires `Owner`-level authority to execute (it's a contract type gated by the owner permission's `operations` bitmap), once an account's `Owner` permission is overwritten with an incorrect, mistyped, or otherwise unreachable key (or with a threshold that can never be met by keys the user actually controls), there is no second step, delay, or "claim" transaction that can undo it. The account's owner-gated capabilities — including the ability to fix the very permission that was just broken — are permanently lost, forcing effective abandonment of the account, exactly mirroring the `Ownable.transferOwnership()`/`renounceOwnership()` one-step risk described in the report (no `pendingOwner` step to catch mistakes before they become irrecoverable).

### Impact Explanation
This is reachable directly from an anonymous broadcast transaction (via `AccountPermissionUpdateServlet`/gRPC `createAccountPermissionUpdate` + `BroadcastTransaction`), requiring only that the caller currently holds sufficient owner-permission weight over their own account. A single mistaken transaction (wrong address, typo, unintended threshold/key removal) permanently and irreversibly locks the account out of all owner-gated operations (further permission updates, and by extension any operation requiring the Owner or Active permissions tied to that key set), with no built-in recovery path in the protocol. This matches the report's "unauthorized/uncontrolled account operation" and DoS-of-account-capability impact category.

### Likelihood Explanation
Any TRON account holder using multisig/permission features can trigger this by broadcasting an `AccountPermissionUpdateContract` transaction; no special node, peer, or governance privilege is needed beyond normal control of the account being updated. The likelihood of accidental misconfiguration is realistic given the permission structure's complexity (multiple key/weight/threshold fields across Owner/Witness/Active permissions), and the actuator provides no warning, delay, or two-phase confirmation to catch such mistakes before they become permanent.

### Recommendation
Add a two-step change process for the `Owner` permission update path (and/or a mandatory invariant check) such as:
1. Require that the proposed new `Owner` permission be confirmed/claimed with a subsequent transaction signed under the new permission before it fully replaces the old one, or
2. At minimum, validate in `AccountPermissionUpdateActuator.checkPermission()`/`validate()` that the resulting Owner permission set is satisfiable and, ideally, that the transaction signer's own key remains part of the new Owner permission unless an explicit acknowledgment/delay flow is used.

### Proof of Concept
1. Account `A` holds default Owner/Active permissions with its own key.
2. `A` broadcasts an `AccountPermissionUpdateContract` (via HTTP `/wallet/accountpermissionupdate` or gRPC) setting the `Owner` permission's `keys` to an address for which no private key is held (or a threshold that cannot be met by keys `A` controls).
3. `AccountPermissionUpdateActuator.validate()` passes all structural checks in `checkPermission()` (valid address format, weight > 0, weight sum ≥ threshold) since these are purely syntactic.
4. `execute()` calls `AccountCapsule.updatePermissions()`, overwriting the stored Owner permission irreversibly.
5. `A` can no longer produce a transaction satisfying the new Owner permission threshold, permanently losing the ability to issue any further owner-gated transactions, including another `AccountPermissionUpdateContract` to fix the mistake.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L47-52)
```java
      byte[] ownerAddress = accountPermissionUpdateContract.getOwnerAddress().toByteArray();
      AccountCapsule account = accountStore.get(ownerAddress);
      account.updatePermissions(accountPermissionUpdateContract.getOwner(),
          accountPermissionUpdateContract.getWitness(),
          accountPermissionUpdateContract.getActivesList());
      accountStore.put(ownerAddress, account);
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

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L148-228)
```java
  @Override
  public boolean validate() throws ContractValidateException {

    if (chainBaseManager == null) {
      throw new ContractValidateException(ActuatorConstant.STORE_NOT_EXIST);
    }

    if (this.any == null) {
      throw new ContractValidateException(ActuatorConstant.CONTRACT_NOT_EXIST);
    }

    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();

    if (dynamicStore.getAllowMultiSign() != 1) {
      throw new ContractValidateException("multi sign is not allowed, "
          + "need to be opened by the committee");
    }
    if (!this.any.is(AccountPermissionUpdateContract.class)) {
      throw new ContractValidateException(
          "contract type error,expected type [AccountPermissionUpdateContract],real type["
              + any.getClass() + "]");
    }
    final AccountPermissionUpdateContract accountPermissionUpdateContract;
    try {
      accountPermissionUpdateContract = any.unpack(AccountPermissionUpdateContract.class);
    } catch (InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractValidateException(e.getMessage());
    }
    byte[] ownerAddress = accountPermissionUpdateContract.getOwnerAddress().toByteArray();
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("invalidate ownerAddress");
    }
    AccountCapsule accountCapsule = accountStore.get(ownerAddress);
    if (accountCapsule == null) {
      throw new ContractValidateException("ownerAddress account does not exist");
    }

    if (!accountPermissionUpdateContract.hasOwner()) {
      throw new ContractValidateException("owner permission is missed");
    }

    if (accountCapsule.getIsWitness()) {
      if (!accountPermissionUpdateContract.hasWitness()) {
        throw new ContractValidateException("witness permission is missed");
      }
    } else {
      if (accountPermissionUpdateContract.hasWitness()) {
        throw new ContractValidateException("account isn't witness can't set witness permission");
      }
    }

    if (accountPermissionUpdateContract.getActivesCount() == 0) {
      throw new ContractValidateException("active permission is missed");
    }
    if (accountPermissionUpdateContract.getActivesCount() > 8) {
      throw new ContractValidateException("active permission is too many");
    }

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
