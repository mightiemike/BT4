### Title
`queryDelegatableResource` precompile returns an inflated "max delegatable" amount that causes `DelegateResourceContract` to revert - ([File: actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java])

### Summary
`FreezeV2Util.queryDelegatableResource()` is exposed via the TVM precompiled-contract interface so that smart contracts can query, on-chain, how much frozen resource (bandwidth/energy) an address can safely delegate before calling `DelegateResourceContract`. This is directly analogous to ERC‑4626's `maxDeposit`: it is supposed to return an amount that, if used in the subsequent state-changing call, will not revert. The value it computes uses a different (unscaled) usage figure than the one actually enforced by `DelegateResourceActuator.validate()`, so a contract that delegates exactly the amount returned by the query can have its `DelegateResourceContract` transaction rejected. [1](#0-0) 

### Finding Description
`queryDelegatableResource` computes the delegatable balance as:
```
frozenV2Resource - v2NetUsage  (or v2EnergyUsage)
```
where `usage` is taken directly from `repository.getAccountNetUsageBalanceAndRestoreSeconds(accountCapsule)` / `getAccountEnergyUsageBalanceAndRestoreSeconds(accountCapsule)` and fed unmodified into `getV2NetUsage`/`getV2EnergyUsage`. [2](#0-1) 

By contrast, `DelegateResourceActuator.validate()`, which is the actual code path that determines whether a `DelegateResourceContract` succeeds, first re-scales the raw usage before computing the same `v2NetUsage`/`v2EnergyUsage` values:
```java
long netUsage = (long) (accountNetUsage * TRX_PRECISION *
    ((double)(dynamicStore.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));
long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage, this.disableJavaLangMath());
if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) {
  throw new ContractValidateException(
      "delegateBalance must be less than or equal to available FreezeBandwidthV2 balance");
}
```
and similarly for energy with `getTotalEnergyWeight()/getTotalEnergyCurrentLimit()`. [3](#0-2) 

Both code paths call the *same* `getV2NetUsage`/`getV2EnergyUsage` helper, but the query path (`FreezeV2Util.queryDelegatableResource`) passes it the raw, unscaled account usage number, while the actuator path first multiplies that usage by `TRX_PRECISION * (totalNetWeight/totalNetLimit)` (or the energy equivalent) — a network-wide weighting factor that can be far larger than 1. As a result, `queryDelegatableResource` will systematically overstate the safely delegatable balance whenever the account has any bandwidth/energy usage and the network weight/limit ratio is not exactly 1×`TRX_PRECISION`. A caller (typically a smart contract that reads this via the precompile to decide a safe delegate amount) that delegates the exact value returned by `queryDelegatableResource` can trigger `ContractValidateException("delegateBalance must be less than or equal to available FreezeBandwidthV2 balance")` (or the energy equivalent) in `DelegateResourceActuator.validate()`, causing the transaction to fail.

### Impact Explanation
Any on-chain smart contract (or off-chain caller relying on the precompile through a contract call) that uses `queryDelegatableResource` to compute a "safe" amount to delegate can have its `DelegateResourceContract` transaction revert, breaking composability and causing unexpected DoS of automated delegation flows (e.g., staking/delegation aggregator contracts built on TVM). This mirrors the `maxDeposit` ERC‑4626 issue: a view/query function that is supposed to guarantee no revert for the returned amount, but does not, in a resource-accounting precompile that is part of java-tron's public TVM API.

### Likelihood Explanation
This triggers under normal conditions whenever an account has nonzero bandwidth/energy usage and calls the query then immediately delegates the returned value — no privileged access or malicious actor is required, it is reachable by any contract call through the standard precompiled-contract interface (`allowTvmFreezeV2`). The discrepancy is deterministic (a scaling-factor mismatch), not a rare edge case, so it will reproduce whenever `totalNetWeight/totalNetLimit` (or the energy equivalent) times `TRX_PRECISION` differs from 1.

### Recommendation
Align the usage calculation in `FreezeV2Util.queryDelegatableResource` with `DelegateResourceActuator.validate()`: apply the same `TRX_PRECISION * (totalNetWeight/totalNetLimit)` (and energy equivalent) scaling to the raw usage value returned by `getAccountNetUsageBalanceAndRestoreSeconds`/`getAccountEnergyUsageBalanceAndRestoreSeconds` before passing it into `getV2NetUsage`/`getV2EnergyUsage`, so the precompile's reported "max delegatable" amount exactly matches what `DelegateResourceActuator` will actually accept.

### Proof of Concept
1. Configure an account with nonzero `NetUsage`/`EnergyUsage` (via normal transaction activity) and freeze some balance via `FreezeV2`.
2. From a TVM contract (or directly), call the `queryDelegatableResource` precompile for that account/type; note the returned value `X`.
3. Submit a `DelegateResourceContract` transaction delegating exactly `X` to a receiver.
4. Observe `DelegateResourceActuator.validate()` throws `ContractValidateException("delegateBalance must be less than or equal to available FreezeBandwidthV2 balance")` (or the energy analog) whenever `dynamicStore.getTotalNetWeight()/dynamicStore.getTotalNetLimit()` (scaled by `TRX_PRECISION`) is not exactly 1, demonstrating that the query's returned "max" amount is not safe to use, contradicting the guarantee such a query function is expected to provide.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java (L142-170)
```java
  public static long queryDelegatableResource(byte[] address, long type, Repository repository) {
    if (!VMConfig.allowTvmFreezeV2()) {
      return 0L;
    }

    AccountCapsule accountCapsule = repository.getAccount(address);
    if (accountCapsule == null) {
      return 0L;
    }

    if (type == 0) {
      // self frozenV2 resource
      long frozenV2Resource = accountCapsule.getFrozenV2BalanceForBandwidth();

      // total Usage.
      Pair<Long, Long> usagePair =
          repository.getAccountNetUsageBalanceAndRestoreSeconds(accountCapsule);
      if (usagePair == null || usagePair.getLeft() == null) {
        return frozenV2Resource;
      }

      long usage = usagePair.getLeft();
      if (usage <= 0) {
        return frozenV2Resource;
      }

      long v2NetUsage = getV2NetUsage(accountCapsule, usage, VMConfig.disableJavaLangMath());
      return max(0L, frozenV2Resource - v2NetUsage, VMConfig.disableJavaLangMath());
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L152-189)
```java
    switch (delegateResourceContract.getResource()) {
      case BANDWIDTH: {
        BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
        processor.updateUsageForDelegated(ownerCapsule);

        long accountNetUsage = ownerCapsule.getNetUsage();
        if (null != this.getTx() && this.getTx().isTransactionCreate()) {
          accountNetUsage += TransactionUtil.estimateConsumeBandWidthSize(dynamicStore,
                  ownerCapsule.getFrozenV2BalanceForBandwidth());
        }
        long netUsage = (long) (accountNetUsage * TRX_PRECISION * ((double)
            (dynamicStore.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));
        long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage,
            this.disableJavaLangMath());
        if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) {
          throw new ContractValidateException(
              "delegateBalance must be less than or equal to available FreezeBandwidthV2 balance");
        }
      }
      break;
      case ENERGY: {
        EnergyProcessor processor = new EnergyProcessor(dynamicStore, accountStore);
        processor.updateUsage(ownerCapsule);

        long energyUsage = (long) (ownerCapsule.getEnergyUsage() * TRX_PRECISION * ((double)
            (dynamicStore.getTotalEnergyWeight()) / dynamicStore.getTotalEnergyCurrentLimit()));
        long v2EnergyUsage = getV2EnergyUsage(ownerCapsule, energyUsage,
            this.disableJavaLangMath());
        if (ownerCapsule.getFrozenV2BalanceForEnergy() - v2EnergyUsage < delegateBalance) {
          throw new ContractValidateException(
                  "delegateBalance must be less than or equal to available FreezeEnergyV2 balance");
        }
      }
      break;
      default:
        throw new ContractValidateException(
            "ResourceCode error, valid ResourceCode[BANDWIDTH、ENERGY]");
    }
```
