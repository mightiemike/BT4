### Title
Single-Step Account Permission Update Allows Irrecoverable Loss of Account Control - ([File: actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java])

### Summary
`AccountPermissionUpdateActuator`, which handles the `AccountPermissionUpdateContract` transaction broadcast by any account holder, replaces an account's `Owner`, `Witness`, and `Active` permission key sets in a single atomic step with no two-step confirmation and only superficial address-format validation. This mirrors the reported bug class in the PNG/Airdrop contracts: any single-step "ownership transfer" whose only safeguard is a syntactic address check can permanently move control to an address the sender does not actually control, with no mechanism to revert.

### Finding Description
The `execute` method directly overwrites the account's permissions once `validate()` passes: [1](#0-0) 

The only checks performed on the new key addresses are done inside `checkPermission`, which calls `DecodeUtil.addressValid`: [2](#0-1) 

`DecodeUtil.addressValid` only validates that the address is 21 bytes long and carries the correct network prefix byte — it does not verify that the address corresponds to an account that exists on-chain, has a known/controlled private key, or is not a burn/black-hole address: [3](#0-2) 

There is no check preventing the new `Owner` permission (or `Active`/`Witness` permissions) from being set to an address nobody holds the key for, and there is no staged/two-step confirmation (e.g., requiring the new key holder to countersign a follow-up transaction) analogous to the Timelock two-step pattern recommended in the external report. Once `execute()` commits the new `Owner` permission via `account.updatePermissions(...)` and `accountStore.put(...)`, the change is final and irreversible on-chain — exactly the "loss of access to privileged functions" scenario the external report describes for `PNG.sol`/`Airdrop.sol`.

### Impact Explanation
If a user (or a compromised/careless multisig participant) submits an `AccountPermissionUpdateContract` with an owner/active key set to an address whose private key is not actually held by any authorized party (e.g., a typo'd address, a contract address, or an address under someone else's exclusive control), the account permanently loses the ability to sign further `Owner`-permission transactions, including any subsequent attempt to fix the permission set. This is a full, unrecoverable loss of account control — matching Impact ~3 in the original report (loss of access to privileged functions), and for TRON accounts this can also strand any TRX/TRC-10/TRC-20 balances and delegated resources tied to that account, since no privileged function can be executed to correct the mistake.

### Likelihood Explanation
Likelihood is realistically low-to-moderate: like the accepted Airdrop/PNG risk, this requires a foot-gun action, either an operator error (mistyped address, wrong key format) or a compromised/malicious cosigner in a multisig setup deliberately setting a permission to an address they alone control while others believe it is shared. Because `checkPermission` provides no semantic validation beyond format-and-uniqueness, and there is no confirmation/undo mechanism, any such misconfiguration is immediately and permanently effective.

### Recommendation
Follow the same mitigation direction suggested for `PNG`/`Airdrop`: introduce a two-step confirmation pattern for `AccountPermissionUpdateContract`, e.g., stage the pending permission change and require a follow-up transaction signed under the *new* permission set (or an explicit acceptance transaction from at least one newly designated key holder) before the update takes effect. At minimum, document this as an accepted risk (as Pangolin did) and provide tooling/UI safeguards (e.g., requiring the caller to prove control of newly added keys via a signature check before submission) to reduce the chance of self-lockout, since `AccountPermissionUpdateActuator.validate()` currently only performs format checks (`DecodeUtil.addressValid`) and never verifies that the new key holder can actually produce a valid signature.

### Proof of Concept
1. Account `A` holds TRX and has default owner/active permissions.
2. `A` broadcasts an `AccountPermissionUpdateContract` transaction, signed under its current `Owner` permission, setting the new `Owner` permission's key list to a syntactically valid TRON address `B` (21 bytes, correct prefix) for which no one present possesses the private key (e.g., a randomly generated/mistyped address).
3. `AccountPermissionUpdateActuator.validate()` passes because `checkPermission` only verifies address format, key uniqueness, weight sums, and threshold — not that `B` is a real, controllable key: [4](#0-3) 
4. `execute()` commits the change: [1](#0-0) 
5. Account `A` can no longer produce a transaction meeting the new `Owner` threshold, permanently losing the ability to update its own permissions, freeze/unfreeze, vote, or perform any other owner-gated operation.

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
