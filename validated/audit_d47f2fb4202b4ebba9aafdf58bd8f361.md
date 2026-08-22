### Title
`DelegateResourceProcessor` (TVM native `delegateresource` opcode) omits the resource-lock invariant enforced by `DelegateResourceActuator`, letting contracts bypass locked-delegation guarantees - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java])

### Summary
The external report describes `restartDca` reusing the "create" code path (`_setupDca`) while omitting several safety checks that the sibling `createDca` function enforces (existing-state checks, system-flag resets, allowance setup), letting an unprivileged caller silently strip protections a legitimate owner relied on. The equivalent pattern exists in java-tron between the ordinary `DelegateResourceContract` transaction path (`DelegateResourceActuator`) and the TVM-reachable native-contract path (`DelegateResourceProcessor`, invoked from `Program.java`'s `delegateResource` opcode, itself reachable from any smart contract via `CALL`/`TriggerSmartContract`). Both paths implement "delegate my frozen V2 resource to another account," but only the actuator path enforces the resource lock and lock-period invariants.

### Finding Description
`DelegateResourceActuator.validate()`/`execute()` supports a `lock`/`lockPeriod` parameter: when `lock` is true, it validates the requested `lockPeriod` against `dynamicStore.getMaxDelegateLockPeriod()`, checks that a new lock period is not shorter than the remaining time of any existing locked delegation (`validRemainTime`), and stores the delegation with a real `expireTime` so the resource cannot be un-delegated before that time [1](#0-0) .

The TVM-reachable equivalent, `DelegateResourceProcessor`, has no lock concept at all: `DelegateResourceParam` carries only `ownerAddress`, `receiverAddress`, `delegateBalance`, and `resourceType` — no `lock`/`lockPeriod` field — [2](#0-1)  and its `validate()`/`execute()` never reference locking; every delegation performed through this path is created with `expireTime = 0` (unlocked) regardless of protocol intent [3](#0-2) , whereas `DelegateResourceActuator.delegateResource` explicitly threads a computed `expireTime` derived from `lockPeriod` [4](#0-3) .

This is the same bug class as `restartDca`: a secondary entry point re-implements the "create" logic but drops safety/invariant-setting steps (there, `isGelatoWatching`/`taskId`/allowance setup; here, the lock/`expireTime` invariant), enabling behavior the primary, audited path was specifically designed to prevent.

### Impact Explanation
Any smart contract can call the `delegateresource` precompiled opcode to delegate its own frozen V2 resource to a receiver and, because no lock is ever applied, can immediately call `undelegateresource` to reclaim it — with none of the lock-period guarantees that receivers of resource delegations (and downstream consumers such as marketplaces or third parties relying on `getMaxDelegateLockPeriod`/`validRemainTime` semantics) are entitled to expect. This diverges resource-delegation accounting behavior between the two protocol-level entry points for an operation that must have identical guarantees, undermining the resource/reward accounting invariants the lock mechanism exists to protect.

### Likelihood Explanation
The opcode is reachable by any contract via ordinary `TriggerSmartContract` broadcast transactions — no privileged role, leaked key, or malicious peer is required, satisfying the "anonymous broadcast transaction/contract call" bar. The gap is unconditional (the field is structurally absent, not just unset), so it is deterministically triggerable on every call.

### Recommendation
1. Short term: add `lock`/`lockPeriod` fields to `DelegateResourceParam`, and port the `DelegateResourceActuator` lock-period validation (`supportMaxDelegateLockPeriod`, `validRemainTime`, max lock period bound) into `DelegateResourceProcessor.validate()`/`execute()`.
2. Long term: document the invariants each `*Contract` operation must uphold regardless of entry point (regular transaction vs. TVM native contract), and add shared/unit tests that assert actuator and processor pairs enforce identical invariants for the same contract type.

### Proof of Concept
1. Deploy/trigger a smart contract that calls the `delegateresource` native opcode (via `Program.delegateResource`) to delegate frozen V2 BANDWIDTH/ENERGY to a receiver address, mirroring a `DelegateResourceContract` with `lock=true` semantics.
2. Because `DelegateResourceParam` has no lock field and `DelegateResourceProcessor` never sets `expireTime` to a nonzero value, the delegation is recorded as unlocked (`expireTime = 0`).
3. Immediately call the `undelegateresource` opcode from the same contract to reclaim the delegated balance — succeeding instantly, in contrast to the same operation performed through `DelegateResourceActuator`, which would have enforced a nonzero lock period and rejected early reclamation.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L211-246)
```java
    boolean lock = delegateResourceContract.getLock();
    if (lock && dynamicStore.supportMaxDelegateLockPeriod()) {
      long lockPeriod = getLockPeriod(true, delegateResourceContract);
      long maxDelegateLockPeriod = dynamicStore.getMaxDelegateLockPeriod();
      if (lockPeriod < 0 || lockPeriod > maxDelegateLockPeriod) {
        throw new ContractValidateException(
            "The lock period of delegate resource cannot be less than 0 and cannot exceed "
                + maxDelegateLockPeriod + "!");
      }

      byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, true);
      DelegatedResourceCapsule delegatedResourceCapsule = delegatedResourceStore.get(key);
      long now = dynamicStore.getLatestBlockHeaderTimestamp();
      if (delegatedResourceCapsule != null) {
        switch (delegateResourceContract.getResource()) {
          case BANDWIDTH: {
            validRemainTime(BANDWIDTH, lockPeriod,
                delegatedResourceCapsule.getExpireTimeForBandwidth(), now);
          }
          break;
          case ENERGY: {
            validRemainTime(ENERGY, lockPeriod,
                delegatedResourceCapsule.getExpireTimeForEnergy(), now);
          }
          break;
          default:
            throw new ContractValidateException(
                "ResourceCode error, valid ResourceCode[BANDWIDTH、ENERGY]");
        }
      }
    }

    if (receiverCapsule.getType() == AccountType.Contract) {
      throw new ContractValidateException(
          "Do not allow delegate resources to contract addresses");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L282-311)
```java
  private void delegateResource(byte[] ownerAddress, byte[] receiverAddress, boolean isBandwidth,
                                long balance, boolean lock, long lockPeriod) {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicPropertiesStore = chainBaseManager.getDynamicPropertiesStore();
    DelegatedResourceStore delegatedResourceStore = chainBaseManager.getDelegatedResourceStore();
    DelegatedResourceAccountIndexStore delegatedResourceAccountIndexStore = chainBaseManager
        .getDelegatedResourceAccountIndexStore();

    // 1. unlock the expired delegate resource
    long now = chainBaseManager.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    delegatedResourceStore.unLockExpireResource(ownerAddress, receiverAddress, now);

    //modify DelegatedResourceStore
    long expireTime = 0;
    if (lock) {
      expireTime = now + lockPeriod * BLOCK_PRODUCED_INTERVAL;
    }
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, lock);
    DelegatedResourceCapsule delegatedResourceCapsule = delegatedResourceStore.get(key);
    if (delegatedResourceCapsule == null) {
      delegatedResourceCapsule = new DelegatedResourceCapsule(ByteString.copyFrom(ownerAddress),
          ByteString.copyFrom(receiverAddress));
    }

    if (isBandwidth) {
      delegatedResourceCapsule.addFrozenBalanceForBandwidth(balance, expireTime);
    } else {
      delegatedResourceCapsule.addFrozenBalanceForEnergy(balance, expireTime);
    }
    delegatedResourceStore.put(key, delegatedResourceCapsule);
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/param/DelegateResourceParam.java (L1-46)
```java
package org.tron.core.vm.nativecontract.param;

import org.tron.protos.contract.Common;

public class DelegateResourceParam {

  private byte[] ownerAddress;

  private byte[] receiverAddress;

  private long delegateBalance;

  private Common.ResourceCode resourceType;

  public byte[] getOwnerAddress() {
    return ownerAddress;
  }

  public void setOwnerAddress(byte[] ownerAddress) {
    this.ownerAddress = ownerAddress;
  }

  public byte[] getReceiverAddress() {
    return receiverAddress;
  }

  public void setReceiverAddress(byte[] receiverAddress) {
    this.receiverAddress = receiverAddress;
  }

  public long getDelegateBalance() {
    return delegateBalance;
  }

  public void setDelegateBalance(long delegateBalance) {
    this.delegateBalance = delegateBalance;
  }

  public Common.ResourceCode getResourceType() {
    return resourceType;
  }

  public void setResourceType(Common.ResourceCode resourceType) {
    this.resourceType = resourceType;
  }
}
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L33-115)
```java
  public void validate(DelegateResourceParam param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    byte[] ownerAddress = param.getOwnerAddress();
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();
    if (!dynamicStore.supportDR()) {
      throw new ContractValidateException("No support for resource delegate");
    }
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddress);
    if (ownerCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);
      throw new ContractValidateException(
          ActuatorConstant.ACCOUNT_EXCEPTION_STR + readableOwnerAddress + NOT_EXIST_STR);
    }
    long delegateBalance = param.getDelegateBalance();
    if (delegateBalance < TRX_PRECISION) {
      throw new ContractValidateException("delegateBalance must be greater than or equal to 1 TRX");
    }

    boolean disableJavaLangMath = VMConfig.disableJavaLangMath();
    switch (param.getResourceType()) {
      case BANDWIDTH: {
        BandwidthProcessor processor = new BandwidthProcessor(ChainBaseManager.getInstance());
        processor.updateUsageForDelegated(ownerCapsule);

        long netUsage = (long) (ownerCapsule.getNetUsage() * TRX_PRECISION * ((double)
            (repo.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));

        long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage, disableJavaLangMath);

        if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) {
          throw new ContractValidateException(
                  "delegateBalance must be less than or equal to available FreezeBandwidthV2 balance");
        }
      }
      break;
      case ENERGY: {
        EnergyProcessor processor =
            new EnergyProcessor(dynamicStore, ChainBaseManager.getInstance().getAccountStore());
        processor.updateUsage(ownerCapsule);

        long energyUsage = (long) (ownerCapsule.getEnergyUsage() * TRX_PRECISION * ((double)
            (repo.getTotalEnergyWeight()) / dynamicStore.getTotalEnergyCurrentLimit()));

        long v2EnergyUsage = getV2EnergyUsage(ownerCapsule, energyUsage, disableJavaLangMath);

        if (ownerCapsule.getFrozenV2BalanceForEnergy() - v2EnergyUsage < delegateBalance) {
          throw new ContractValidateException(
                  "delegateBalance must be less than or equal to available FreezeEnergyV2 balance");
        }
      }
      break;
      default:
        throw new ContractValidateException(
            "Unknown ResourceCode, valid ResourceCode[BANDWIDTH、ENERGY]");
    }

    byte[] receiverAddress = param.getReceiverAddress();

    if (!DecodeUtil.addressValid(receiverAddress)) {
      throw new ContractValidateException("Invalid receiverAddress");
    }
    if (Arrays.equals(receiverAddress, ownerAddress)) {
      throw new ContractValidateException(
          "receiverAddress must not be the same as ownerAddress");
    }
    AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
    if (receiverCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(receiverAddress);
      throw new ContractValidateException(
          ActuatorConstant.ACCOUNT_EXCEPTION_STR
              + readableOwnerAddress + NOT_EXIST_STR);
    }
    if (receiverCapsule.getType() == Protocol.AccountType.Contract) {
      throw new ContractValidateException(
          "Do not allow delegate resources to contract addresses");
    }
  }
```
