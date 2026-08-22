This is a real behavioral divergence introduced in the VM native-contract path compared to the ordinary transaction actuators.

### Title
FreezeBalanceProcessor auto-creates arbitrary receiver accounts via smart-contract freeze precompile, bypassing the existence checks enforced by regular actuators - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java])

### Summary
`FreezeBalanceProcessor.validate` (invoked by a TVM native/precompiled freeze operation reachable from any deployed smart contract) silently calls `repo.createNormalAccount(receiverAddress)` when the attacker-supplied `receiverAddress` does not yet exist, then `execute`/`delegateResource` mutates that freshly-created account's `AcquiredDelegatedFrozenBalanceForBandwidth/Energy` field [1](#0-0) [2](#0-1) . This is notably inconsistent with the equivalent, newer transaction-level actuator `DelegateResourceActuator.validate`, which explicitly rejects delegation to a receiver that doesn't already exist in `AccountStore` [3](#0-2) .

### Finding Description
`FreezeBalanceProcessor.validate` computes `receiverAddress` from `param.getReceiverAddress()` and, if `!isEqual(ownerAddress, receiverAddress)`, looks up the receiver in the `AccountStore`; if absent it calls `repo.createNormalAccount(receiverAddress)` unconditionally [4](#0-3) . No address-format validation (`DecodeUtil.addressValid`) is performed on `receiverAddress` in this class, and no signature/authorization is required from the receiver — only the caller/owner's transaction signature is checked (standard `TransactionCapsule.validateSignature`, which only covers `ownerAddress`). `execute` then unconditionally calls `delegateResource`, which fetches the (now freshly created) `receiverCapsule` and calls `addAcquiredDelegatedFrozenBalanceForBandwidth`/`Energy` and persists it via `repo.updateAccount` [5](#0-4) .

The mirrored, legacy, transaction-level actuator `FreezeBalanceActuator` has essentially the same behavior for `FreezeBalanceContract` with a `receiverAddress` (it creates the account implicitly since `accountStore.get` returning null would cause an NPE downstream unless it exists — but actually looking at it, `FreezeBalanceActuator.validate` explicitly rejects a non-existent receiver, at line 254-260) [6](#0-5) . Similarly the newer `DelegateResourceActuator.validate` rejects delegation to a nonexistent receiver [3](#0-2) . Only the VM-reachable `FreezeBalanceProcessor` diverges from this pattern by auto-creating the account instead of rejecting the transaction.

This does allow an unprivileged smart-contract caller (deploying/calling a contract that invokes the TVM freeze-resource native operation) to force-create an `AccountCapsule` at an arbitrary 21-byte address of their choosing and set nonzero `AcquiredDelegatedFrozenBalanceForBandwidth`/`Energy` on it, without any signature or consent from that address's key holder. This is broadly consistent with the way TRON already permits account creation as a side effect of ordinary value transfer (`TransferContract`) — sending TRX to an unregistered address also creates an `AccountCapsule` there, at a cost (bandwidth/account-creation fee). The key question for scoped impact is whether this native-contract path pays a comparable creation fee/cost or is cheaper/free due to being reachable from inside a contract execution, and whether it can write arbitrary reserved-looking address prefixes.

### Impact Explanation
Concrete impact is limited to unsolicited account-record creation and a delegated-resource-balance field write for addresses the attacker doesn't control, at the cost of 1 TRX (`TRX_PRECISION` minimum) per new account plus whatever bandwidth/energy the invoking contract call consumes [7](#0-6) . This does not steal funds, create unbacked TRX, or grant the attacker control over the victim's future account — the "AcquiredDelegatedFrozenBalance" field only reflects resources delegated *to* that account by the attacker, it does not give the attacker any claim over funds the real owner later deposits. This matches at most an account-store bloat / unauthorized-state-mutation class finding, not an asset theft or consensus divergence issue, since account creation via first-touch (including by third parties, e.g., sending TRX) is an established and intended TRON behavior.

### Likelihood Explanation
Preconditions are low: any funded account can deploy/call a contract that triggers the TVM freeze-delegate native operation with a chosen `receiverAddress`, paying only the transaction's energy/bandwidth cost plus the 1 TRX minimum freeze amount per target address. This is repeatable at scale, bounded only by the attacker's available TRX and by per-transaction resource limits, so the "growing account store disproportionately to fee paid" characterization is plausible if the per-account cost is materially lower than the cost the protocol otherwise charges for account creation via ordinary transfers (this specific fee-parity comparison could not be fully confirmed from the available files, since the exact TVM opcode gas/energy cost for this native contract and the exact "new account" bandwidth/fee applied elsewhere were not located in the retrieved files).

### Recommendation
Make `FreezeBalanceProcessor.validate` consistent with `DelegateResourceActuator.validate`: reject the transaction with a `ContractValidateException` when `receiverAddress` does not already exist in the `AccountStore`, instead of silently calling `repo.createNormalAccount(receiverAddress)`. Also add `DecodeUtil.addressValid(receiverAddress)` validation, matching the checks present in `FreezeBalanceActuator` and `DelegateResourceActuator`.

### Proof of Concept
```java
// Conceptual JUnit outline (exact Repository/AccountCapsule test scaffolding
// would need to match the existing native-contract test harness, e.g. as used
// in framework/src/test/java/org/tron/common/runtime/vm/FreezeV2Test.java)
byte[] unusedReceiver = randomAddress(); // never touched on-chain
assertNull(repo.getAccount(unusedReceiver));

FreezeBalanceParam param = new FreezeBalanceParam();
param.setOwnerAddress(ownerAddress);
param.setReceiverAddress(unusedReceiver);
param.setFrozenBalance(TRX_PRECISION); // 1 TRX
param.setResourceType(Common.ResourceCode.BANDWIDTH);

FreezeBalanceProcessor processor = new FreezeBalanceProcessor();
processor.validate(param, repo);   // does NOT throw; instead creates account
processor.execute(param, repo);

AccountCapsule created = repo.getAccount(unusedReceiver);
assertNotNull(created); // account created without receiver's signature
assertTrue(created.getAcquiredDelegatedFrozenBalanceForBandwidth() > 0);
```
This confirms `validate()`/`execute()` create and mutate the account state of `unusedReceiver` with no signature check on that address, in contrast to `DelegateResourceActuator.validate`, which throws `ACCOUNT_EXCEPTION_STR ... NOT_EXIST_STR` for the same scenario [3](#0-2) .

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L30-36)
```java
    if (frozenBalance <= 0) {
      throw new ContractValidateException("FrozenBalance must be positive");
    } else if (frozenBalance < TRX_PRECISION) {
      throw new ContractValidateException("FrozenBalance must be greater than or equal to 1 TRX");
    } else if (frozenBalance > ownerCapsule.getBalance()) {
      throw new ContractValidateException("FrozenBalance must be less than or equal to accountBalance");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L54-70)
```java
    // validate for delegating resource
    byte[] receiverAddress = param.getReceiverAddress();
    if (!FastByteComparisons.isEqual(ownerAddress, receiverAddress)) {
      param.setDelegating(true);

      // check if receiver account exists. if not, then create a new account
      AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
      if (receiverCapsule == null) {
        receiverCapsule = repo.createNormalAccount(receiverAddress);
      }

      // forbid delegating resource to contract account
      if (receiverCapsule.getType() == Protocol.AccountType.Contract) {
        throw new ContractValidateException(
            "Do not allow delegate resources to contract addresses");
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L137-168)
```java
  private void delegateResource(
      byte[] ownerAddress,
      byte[] receiverAddress,
      long frozenBalance,
      long expireTime,
      boolean isBandwidth,
      Repository repo) {
    byte[] key = DelegatedResourceCapsule.createDbKey(ownerAddress, receiverAddress);

    // insert or update DelegateResource
    DelegatedResourceCapsule delegatedResourceCapsule = repo.getDelegatedResource(key);
    if (delegatedResourceCapsule == null) {
      delegatedResourceCapsule = new DelegatedResourceCapsule(
          ByteString.copyFrom(ownerAddress),
          ByteString.copyFrom(receiverAddress));
    }
    if (isBandwidth) {
      delegatedResourceCapsule.addFrozenBalanceForBandwidth(frozenBalance, expireTime);
    } else {
      delegatedResourceCapsule.addFrozenBalanceForEnergy(frozenBalance, expireTime);
    }
    repo.updateDelegatedResource(key, delegatedResourceCapsule);

    // do delegating resource to receiver account
    AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
    if (isBandwidth) {
      receiverCapsule.addAcquiredDelegatedFrozenBalanceForBandwidth(frozenBalance);
    } else {
      receiverCapsule.addAcquiredDelegatedFrozenBalanceForEnergy(frozenBalance);
    }
    repo.updateAccount(receiverCapsule.createDbKey(), receiverCapsule);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L203-209)
```java
    AccountCapsule receiverCapsule = accountStore.get(receiverAddress);
    if (receiverCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(receiverAddress);
      throw new ContractValidateException(
          ActuatorConstant.ACCOUNT_EXCEPTION_STR
              + readableOwnerAddress + NOT_EXIST_STR);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L254-260)
```java
      AccountCapsule receiverCapsule = accountStore.get(receiverAddress);
      if (receiverCapsule == null) {
        String readableOwnerAddress = StringUtil.createReadableString(receiverAddress);
        throw new ContractValidateException(
            ActuatorConstant.ACCOUNT_EXCEPTION_STR
                + readableOwnerAddress + NOT_EXIST_STR);
      }
```
