### Title
`getCanDelegatedMaxSize` (`calcCanDelegatedBandWidthMaxSize`/`calcCanDelegatedEnergyMaxSize`) returns a non-zero delegable amount even when resource delegation is disabled by the committee, contradicting `DelegateResourceActuator` - (File: `framework/src/main/java/org/tron/core/Wallet.java`)

### Summary
This is the same bug class as the C4 finding: a "max/preview" accessor that does not honor a global disable switch that the actual state-mutating operation enforces, so the accessor reports an amount as usable when the real operation would revert. In java-tron, the GRPC/HTTP endpoint `getCanDelegatedMaxSize` computes a "can-delegate" amount via `Wallet.calcCanDelegatedBandWidthMaxSize` / `calcCanDelegatedEnergyMaxSize` without checking `DynamicPropertiesStore.supportDR()` or `supportUnfreezeDelay()`, while `DelegateResourceActuator.validate()` requires both to be enabled before actually delegating.

### Finding Description
`DelegateResourceActuator.validate()` explicitly gates the DelegateResource transaction on two dynamic parameters: [1](#0-0) 

If either `supportDR()` or `supportUnfreezeDelay()` is turned off by the committee, any `DelegateResourceContract` transaction will fail validation with `ContractValidateException`.

However, the read-only API `Wallet.getCanDelegatedMaxSize` → `calcCanDelegatedBandWidthMaxSize` / `calcCanDelegatedEnergyMaxSize`, which is exposed to any RPC/HTTP caller to query how much resource an address can delegate, performs none of these checks: [2](#0-1) 

It computes and returns a positive `maxSize` purely from frozen balances and usage, with no reference to `supportDR()`/`supportUnfreezeDelay()`. Compare this with `getAvailableUnfreezeCount`/`FreezeV2Util.queryDelegatableResource`, which does correctly gate on the analogous feature switch `VMConfig.allowTvmFreezeV2()`: [3](#0-2) 

This confirms the pattern exists elsewhere in the codebase and was simply omitted for the delegation-max-size view function, exactly mirroring the ERC-4626 `maxDeposit`/`maxMint` vs. `maxWithdraw`/`maxRedeem` inconsistency in the referenced report.

### Impact Explanation
Any wallet, exchange, delegation-market dApp, or bot that calls the `GetCanDelegatedMaxSize` gRPC/HTTP API to decide how much bandwidth/energy it can safely delegate will receive a non-zero, seemingly valid amount even while the feature is disabled network-wide. Acting on this value (submitting a `DelegateResourceContract`) will always fail on-chain, wasting bandwidth/fee-limit reservation and potentially breaking automated systems that assume the queried value is actionable. This is a state-handling/spec inconsistency between a view API and the actual actuator, matching the Medium-severity rationale given in the original report ("the function breaks the standard/consistency, though not directly causing loss of funds").

### Likelihood Explanation
Triggering the inconsistency requires no privileged access — any external caller can invoke the public `getCanDelegatedMaxSize` API at any time; the only precondition is that the chain committee has (or later does) toggle `supportDR`/`supportUnfreezeDelay` off, which is a normal, already-supported governance action in the dynamic properties store. The reachable path is a plain read-only RPC call, so exploitability by an anonymous caller of the inconsistency is trivial; the only variable is the committee-controlled flag state, which is out of caller control but not privileged-attacker dependent for the disclosure itself.

### Recommendation
Add the same guard used in `DelegateResourceActuator.validate()` to `Wallet.calcCanDelegatedBandWidthMaxSize` and `calcCanDelegatedEnergyMaxSize` (or in `getCanDelegatedMaxSize`): return `0` immediately if `dynamicStore.supportDR()` is false or `dynamicStore.supportUnfreezeDelay()` is false, so the view function's output stays consistent with what the actuator will actually allow.

### Proof of Concept
1. Committee sets `supportDR` (or `supportUnfreezeDelay`) to disabled via the corresponding proposal.
2. An account has a positive `getFrozenV2BalanceForBandwidth()`/`getFrozenV2BalanceForEnergy()` and low usage.
3. Call `Wallet.getCanDelegatedMaxSize(ownerAddress, resourceType)` via the exposed gRPC/HTTP API — it returns a positive `maxSize` (e.g., the account's free frozen balance).
4. Submit a `DelegateResourceContract` for that amount — `DelegateResourceActuator.validate()` throws `ContractValidateException("No support for resource delegate")`/`"Not support Delegate resource transaction..."`, proving the queried value was never actually delegable, i.e., the view function and actuator diverge exactly as in the referenced report.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L118-125)
```java
    if (!dynamicStore.supportDR()) {
      throw new ContractValidateException("No support for resource delegate");
    }

    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support Delegate resource transaction,"
          + " need to be opened by the committee");
    }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L994-1038)
```java
  public long calcCanDelegatedBandWidthMaxSize(
          ByteString ownerAddress) {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    AccountCapsule ownerCapsule = accountStore.get(ownerAddress.toByteArray());
    if (ownerCapsule == null) {
      return 0L;
    }

    BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
    processor.updateUsage(ownerCapsule);

    long accountNetUsage = ownerCapsule.getNetUsage();
    accountNetUsage += TransactionUtil.estimateConsumeBandWidthSize(dynamicStore,
            ownerCapsule.getFrozenV2BalanceForBandwidth());

    long netUsage = (long) (accountNetUsage * TRX_PRECISION * ((double)
            (dynamicStore.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));

    long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage, dynamicStore.disableJavaLangMath());

    long maxSize = ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage;
    return max(0, maxSize, dynamicStore.disableJavaLangMath());
  }

  public long calcCanDelegatedEnergyMaxSize(ByteString ownerAddress) {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    AccountCapsule ownerCapsule = accountStore.get(ownerAddress.toByteArray());
    if (ownerCapsule == null) {
      return 0L;
    }

    EnergyProcessor processor = new EnergyProcessor(dynamicStore, accountStore);
    processor.updateUsage(ownerCapsule);

    long energyUsage = (long) (ownerCapsule.getEnergyUsage() * TRX_PRECISION * ((double)
            (dynamicStore.getTotalEnergyWeight()) / dynamicStore.getTotalEnergyCurrentLimit()));

    long v2EnergyUsage = getV2EnergyUsage(ownerCapsule, energyUsage,
        dynamicStore.disableJavaLangMath());

    long maxSize =  ownerCapsule.getFrozenV2BalanceForEnergy() - v2EnergyUsage;
    return max(0, maxSize, dynamicStore.disableJavaLangMath());
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java (L142-150)
```java
  public static long queryDelegatableResource(byte[] address, long type, Repository repository) {
    if (!VMConfig.allowTvmFreezeV2()) {
      return 0L;
    }

    AccountCapsule accountCapsule = repository.getAccount(address);
    if (accountCapsule == null) {
      return 0L;
    }
```
