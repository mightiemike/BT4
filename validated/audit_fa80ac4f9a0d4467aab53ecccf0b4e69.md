## Finding

### Title
Account owner permission updates via `AccountPermissionUpdateActuator` can permanently brick an account with no recovery mechanism - (File: `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java`)

### Summary
The `AccountPermissionUpdateContract` mechanism, executed by `AccountPermissionUpdateActuator`, lets an account owner rewrite the `Owner`, `Witness`, and `Active` permissions that govern which keys (and what weighted threshold of them) can authorize future transactions for that account. Once a permission update is applied, it is written directly to the `AccountCapsule` with no timelock, no staged/two-step confirmation, and no fallback authority. If the new owner permission data is wrong (e.g. produced by a buggy client/wallet script that mistypes an address, assigns weight to an unintended key, or otherwise sets up an owner set that the user does not actually control), the account becomes permanently unable to authorize any further `AccountPermissionUpdateContract` (or any other transaction requiring owner/active signatures), because signature verification is always checked against the *currently stored* permission. This mirrors the reported Keystore.sol issue: a bad value written by a flawed creation/update path leaves the account bricked with no recovery path.

### Finding Description
`AccountPermissionUpdateActuator.execute()` unconditionally overwrites the account's permissions: [1](#0-0) 

`validate()` and the helper `checkPermission()` only check structural/self-consistency properties of the new permission set — key count bounds, distinct addresses, positive weights, and that `weightSum >= threshold` — never that the resulting owner set is actually controllable by the submitting party going forward: [2](#0-1) 

Because the on-chain protocol has no way to verify that an address in the new `Permission.keys` list corresponds to a key the account owner actually possesses, a single incorrect address (typo, wrong derivation, copy-paste error from a buggy script) that still satisfies `weightSum >= threshold` passes validation and is committed permanently. There is no subsequent contract type, admin function, or governance proposal in the actuator set that can restore or override an account's owner permission once set — the same "no recovery mechanism" gap flagged in the Keystore.sol report for `handleUpdates()`.

### Impact Explanation
An account whose owner permission is corrupted this way permanently loses the ability to:
- Submit any further `AccountPermissionUpdateContract` to fix the mistake (requires the very owner signature that is now unreachable/invalid).
- Authorize any `Active`-permission-gated operation if the owner set also controlled or overlapped active permission recovery paths.

This is a permanent denial-of-service/invalid-state condition against the account's own funds and governance capability — funds and resources (bandwidth/energy, staked TRX, voting rights) tied to that address become unmanageable, matching the "invalid-state/halt" impact class.

### Likelihood Explanation
The failure mode does not require an attacker; it only requires a bug in an off-chain signing/wallet tool (the same trigger class described in the original report — "a bug in the creation script"). Given that `AccountPermissionUpdateContract` is a normal user-facing, unprivileged operation (multisig owners frequently rotate keys), and the protocol performs no semantic validation of address reachability, the likelihood of an accidental self-brick is realistic wherever automated or scripted permission updates are used (e.g., custodial rotation tooling, exchange key-management automation).

### Recommendation
Consider adding a safety mechanism for owner-permission changes, such as:
- A time-delayed activation window for new owner permissions (allowing the current owner to cancel before it takes effect), similar to timelock-based key-rotation patterns.
- Requiring a "proof of control" step (e.g., a follow-up transaction signed by the *new* set of keys within a grace period) before the old permission is fully retired.
- Providing an emergency governance-level recovery path (e.g., a super-representative-gated contract) for permanently locked accounts, analogous to what the Stackup/Spearbit fix (PR 57) implemented for Keystore.sol.

### Proof of Concept
1. Account `A` currently has `Owner` permission `{threshold=1, keys=[A:1]}`.
2. A wallet/script bug builds an `AccountPermissionUpdateContract` intending to add a backup key `B`, but due to a transcription bug encodes an unintended address `X` (which nobody controls) with `weight=1` and `threshold=1`, satisfying `weightSum(1) >= threshold(1)`.
3. `checkPermission()` passes (key count > 0, weight > 0, weightSum >= threshold, valid address format for `X`).
4. `execute()` calls `account.updatePermissions(...)` and persists the new owner permission irreversibly: [3](#0-2) 
5. Account `A` can no longer produce a valid owner signature for any future `AccountPermissionUpdateContract`, because only `X` (uncontrolled) can authorize owner-level actions going forward — the account is permanently bricked with no recovery path in the actuator/contract set.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L44-52)
```java
    try {
      accountPermissionUpdateContract = any.unpack(AccountPermissionUpdateContract.class);

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
