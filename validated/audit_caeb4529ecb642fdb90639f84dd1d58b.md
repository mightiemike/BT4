### Title
Missing zero-address check for permission keys in `AccountPermissionUpdateActuator` can permanently lock an account's owner/active/witness permissions - (File: `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java`)

### Summary
`AccountPermissionUpdateActuator` allows any account to reassign its `Owner`, `Witness`, and `Active` permissions to arbitrary key addresses via a broadcast `AccountPermissionUpdateContract` transaction. The only address-format validation applied to each permission key is `DecodeUtil.addressValid`, which never rejects the all-zero (or any other unrecoverable) address payload, mirroring the `Governable.transferGovernor()` bug class where a privileged/administrative "owner" role can be reassigned to an address nobody controls, permanently disabling all functions gated by that permission.

### Finding Description
`checkPermission()` validates each `Key` in a permission only for: address length/prefix (`DecodeUtil.addressValid`), positive weight, distinctness, and aggregate threshold — it does not check whether the resulting address is a "burn"/all-zero-payload address that corresponds to no controllable private key. [1](#0-0) 

`DecodeUtil.addressValid` only checks non-empty, exact length (`ADDRESS_SIZE/2`), and the correct network prefix byte — it does not check whether the remaining bytes are all zero. [2](#0-1) 

Once `validate()` passes, `execute()` unconditionally overwrites the account's owner/witness/active permissions with the attacker/user-supplied values via `account.updatePermissions(...)`, with no rollback path. [3](#0-2) 

This is the same bug class as the reported `Governable.transferGovernor()` issue: a state-mutating operation that reassigns a controlling/administrative credential (here, the account's `Owner` permission keys) accepts an address value that can never be used to sign again, with no explicit rejection of degenerate/zero-value addresses.

### Impact Explanation
If an `Owner` permission's key set is updated to reference addresses whose private keys are unknown/unrecoverable (including the conventional zero-payload address), the account irrevocably loses the ability to satisfy its owner-permission threshold. Since `AccountPermissionUpdateContract` itself requires owner-permission authorization to execute, this also makes it impossible to ever call `AccountPermissionUpdateActuator` again for that account, permanently locking multi-sig/permission management for the account (denial of service on account governance), analogous to losing `onlyGovernor` access forever in the reported bug.

### Likelihood Explanation
This is reachable by any account holder that owns/controls their own account and has `AllowMultiSign` enabled, via a normal broadcast transaction (`AccountPermissionUpdateContract`), with no special privilege beyond normal owner-permission signing rights over their own account — it requires only an accidental or malformed key input, matching the "unprivileged/self-inflicted misconfiguration" nature of the original report.

### Recommendation
In `AccountPermissionUpdateActuator.checkPermission()`, explicitly reject keys whose address payload is all-zero (or otherwise a known unusable/burn address), in addition to the existing `DecodeUtil.addressValid` length/prefix check, for all of `Owner`, `Witness`, and `Active` permission key lists.

### Proof of Concept
1. Enable multi-sign (`AllowMultiSign=1`) for an account.
2. Broadcast an `AccountPermissionUpdateContract` transaction where the `Owner` permission's single/only key has address `41` + 20 zero bytes (passes `DecodeUtil.addressValid` since length and prefix are correct) with sufficient weight to meet the threshold.
3. `validate()` succeeds since `checkPermission` only checks format/weight/threshold, not the specific zero value.
4. `execute()` commits the new owner permission via `account.updatePermissions(...)`.
5. The account can never again produce a valid owner-permission signature (no controllable key for the zero address), permanently locking `AccountPermissionUpdateContract` and any other owner-permission-gated operation for that account.

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

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L105-111)
```java
    for (Key key : permission.getKeysList()) {
      if (!DecodeUtil.addressValid(key.getAddress().toByteArray())) {
        throw new ContractValidateException("key is not a validate address");
      }
      if (key.getWeight() <= 0) {
        throw new ContractValidateException("key's weight should be greater than 0");
      }
```

**File:** common/src/main/java/org/tron/common/utils/DecodeUtil.java (L15-33)
```java
  public static boolean addressValid(byte[] address) {
    if (ArrayUtils.isEmpty(address)) {
      logger.warn("Warning: Address is empty !!");
      return false;
    }
    if (address.length != ADDRESS_SIZE / 2) {
      logger.warn(
          "Warning: Address length need " + ADDRESS_SIZE + " but " + address.length
              + " !!");
      return false;
    }

    if (address[0] != addressPreFixByte) {
      logger.warn("Warning: Address need prefix with " + addressPreFixByte + " but "
          + address[0] + " !!");
      return false;
    }
    return true;
  }
```
