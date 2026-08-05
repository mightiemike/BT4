### Title
Single-step, irreversible Owner permission update via `AccountPermissionUpdateContract` can permanently lock an account out of its own permission management - (File: `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java`)

### Summary
`AccountPermissionUpdateActuator` lets any account holder replace their account's Owner/Witness/Active permissions in a single atomic transaction, with no staged "propose then confirm" mechanism. This mirrors the reported bug class of single-step, irrecoverable ownership transfer: if the new Owner permission is misconfigured (e.g., referencing key addresses the caller does not actually control, or an unreachable weight/threshold combination), the account permanently loses the ability to ever change its own permissions again, since only the current Owner permission can authorize a subsequent `AccountPermissionUpdateContract`.

### Finding Description
`execute()` unconditionally applies the new permissions supplied in the contract and persists them to the `AccountStore` in one step: [1](#0-0) 

`validate()` performs structural checks (key validity, weight sums, thresholds, permission counts) via `checkPermission()`, but it never verifies that the account submitting the transaction actually controls the private keys being installed into the new Owner permission — that verification is external, based on multisig weight over the *current* permission at the transaction-signature level, not on the *new* permission being written: [2](#0-1) 

There is no two-step "propose new owner / new owner claims" pattern (unlike, e.g., a `pendingOwner` accept-transfer design). Once an `AccountPermissionUpdateContract` with a bad Owner permission (unreachable threshold, or keys whose private keys nobody holds) is committed on-chain, the account's Owner permission becomes permanently unusable, since any future `AccountPermissionUpdateContract` for that address must itself satisfy the (now broken) Owner permission threshold, as enforced by the general permission-check pipeline used for all transactions: [3](#0-2) 

This is architecturally identical to the reported bug class: a single, irreversible critical-permission change with no staged confirmation step, so any mistake is permanent.

### Impact Explanation
A locked Owner permission permanently prevents the account from ever again modifying its Owner/Witness/Active permission structure via `AccountPermissionUpdateContract`, since that contract type itself always requires satisfying the Owner permission (permission ID 0). This is a permanent state divergence/DoS on the account's governance capability — the account can be left unable to rotate multisig keys, add/remove signers, or recover from a compromised or lost key, with no on-chain recovery path. While the account may still retain some active-permission-gated capability, the Owner-level control is irrecoverably bricked, matching the "invalid-state/halt" impact category.

### Likelihood Explanation
Reachable by any unprivileged account holder on mainnet once multisig is enabled (`getAllowMultiSign() == 1`), simply by submitting a single `AccountPermissionUpdateContract` transaction with a flawed Owner permission (e.g., listing addresses whose keys are not actually held, or setting a threshold not satisfiable by controlled keys). No special/trusted role is required to trigger it — the actuator performs the change in one step for any caller, matching the "no confirmation/second step" root cause in the source report.

### Recommendation
Introduce a two-step permission-update flow for the Owner permission specifically: (1) a `proposeOwnerPermission` step that stores the intended new Owner permission without activating it, and (2) a confirmation transaction, signed under the *proposed* new permission, that activates it and replaces the old Owner permission. This mirrors the "pendingOwner"-style two-step pattern from the source report and prevents irrecoverable lockouts from a single malformed `AccountPermissionUpdateContract`.

### Proof of Concept
1. Enable multisig (`saveAllowMultiSign(1)`) for a test chain, as done in `AccountPermissionUpdateActuatorTest.createCapsule()`.
2. Submit an `AccountPermissionUpdateContract` for account `OWNER_ADDRESS` whose Owner permission lists only key addresses the caller does not hold private keys for (structurally valid: weights sum ≥ threshold, ≤5 keys, distinct addresses) — this passes `checkPermission()` and `validate()` as shown in the existing test flow in `AccountPermissionUpdateActuatorTest.successUpdatePermissionKey()`.
3. `execute()` commits the new Owner permission via `account.updatePermissions(...)` and `accountStore.put(...)`.
4. Any subsequent attempt to submit another `AccountPermissionUpdateContract` for that address will fail permission-weight checks in `TransactionUtil.getTransactionSignWeight` / signature verification, because no held key satisfies the new Owner permission's threshold — the account's Owner-level governance is now permanently unrecoverable.

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

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L148-229)
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
  }
```

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L224-253)
```java
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
```
