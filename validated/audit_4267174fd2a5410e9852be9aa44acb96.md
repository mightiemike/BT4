### Title
Cost-free manipulation of `TotalNetWeight`/`TotalEnergyWeight` via Freeze/UnfreezeV2 can distort resource-delegation solvency checks - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java])

### Summary
The Flayer bug lets an attacker freely inflate/deflate an ERC20 `totalSupply` via fee-less `deposit`/`redeem`, which feeds directly into a global `utilizationRate` used by a critical solvency/health check (`calculateCompoundedFactor` → `unlockPrice` → `getProtectedListingHealth`), letting the attacker force other users' positions into liquidation. java-tron has a structurally identical pattern: `TotalNetWeight`/`TotalEnergyWeight` are global aggregates that any account can move up or down at essentially no cost via `FreezeBalanceV2`/`UnfreezeBalanceV2` (both from a normal transaction and from a smart contract via the native TVM processors), and this same global aggregate is read live inside `DelegateResourceActuator`/`DelegateResourceProcessor`'s solvency check that gates how much resource an account is permitted to delegate.

### Finding Description
`FreezeBalanceV2Actuator`/`FreezeBalanceV2Processor` and `UnfreezeBalanceV2Actuator`/`UnfreezeBalanceV2Processor` mutate `DynamicPropertiesStore.addTotalNetWeight` / `addTotalEnergyWeight` proportionally to the amount frozen/unfrozen, with no fee and no cooldown beyond the unfreeze-lock period: [1](#0-0) [2](#0-1) 

`TotalNetWeight`/`TotalEnergyWeight` is then read live (not snapshotted) inside `DelegateResourceActuator.validate()` and its TVM-native twin `DelegateResourceProcessor.validate()` to compute a "used balance in V2 terms" that gates the maximum amount an account may delegate: [3](#0-2) [4](#0-3) 

Because `FreezeBalanceV2Contract`/`UnfreezeBalanceV2Contract` are also exposed as TVM native contracts (`FreezeBalanceV2Processor`, `UnfreezeBalanceV2Processor`, `DelegateResourceProcessor` all live under `org.tron.core.vm.nativecontract`), a single smart-contract transaction can atomically: (1) freeze/unfreeze a large TRX amount to shift `TotalNetWeight`/`TotalEnergyWeight` in the direction that minimizes the computed `netUsage`/`energyUsage` term, (2) call delegate-resource while the distorted ratio makes `getFrozenV2BalanceForBandwidth() - v2NetUsage` (or the energy equivalent) look artificially larger than it truly is, and (3) revert the weight shift afterward in the same transaction — the exact "deposit → exploit distorted ratio → redeem" pattern described in the Flayer report. This lets an account pass the delegation-eligibility check with a `delegateBalance` that is not actually backed by real spare capacity, corrupting the account's own resource accounting (`addFrozenBalanceForBandwidthV2(-delegateBalance)` executes unconditionally once validate passes) as well as the receiver's `AcquiredDelegatedFrozenV2Balance` bookkeeping.

### Impact Explanation
This breaks the intended solvency invariant of resource delegation: an account should never be able to delegate resource it doesn't actually hold in reserve. If the check can be bypassed via a transient, self-reversing manipulation of the global weight denominator, an attacker can create delegated resource records that are not properly backed, leading to an inconsistent state between `FrozenBalanceForBandwidthV2`/`FrozenBalanceForEnergyV2` and the account's real remaining, already-consumed bandwidth/energy. This is an accounting/invalid-state impact analogous in class (though not in magnitude) to the forced-liquidation impact in the original report, since both stem from a costless, attacker-controlled global aggregate feeding directly into another account's or the protocol's solvency check.

### Likelihood Explanation
Freeze/UnfreezeV2 and DelegateResource are all permissionless, unprivileged actions, and the native-contract versions make them composable within a single atomic TVM transaction, which is required to realize the "manipulate then immediately undo" pattern. The likelihood is moderated by: (a) `UnfreezeBalanceV2` normally has withdrawal-lock semantics for the *balance*, but the `TotalNetWeight`/`TotalEnergyWeight` counters themselves are updated immediately regardless of lock, so the weight-shift itself is instantaneous; (b) the actual magnitude of the ratio distortion an attacker can achieve within one transaction is bounded by how much TRX they can freeze/unfreeze relative to the existing global weight, so on a live mainnet with a large existing `TotalNetWeight`, the practical effect may be small unless the attacker commands a very large TRX balance.

### Recommendation
- Snapshot `TotalNetWeight`/`TotalEnergyWeight` at the start of the transaction (or use a time-weighted/delayed value) rather than reading the live, same-transaction-mutable value inside `DelegateResourceActuator`/`DelegateResourceProcessor`'s eligibility checks.
- Alternatively, disallow calling `FreezeBalanceV2`/`UnfreezeBalanceV2` and `DelegateResource` as composable native contracts within the same top-level transaction, or enforce a same-block/same-tx cooldown between a weight-changing freeze/unfreeze operation and any check that depends on `TotalNetWeight`/`TotalEnergyWeight` for the same account.
- Recompute the solvency check based purely on the account's own frozen/delegated ledger rather than a globally-mutable ratio, if the intent is only to ensure the account isn't delegating more than it has frozen.

### Proof of Concept
Not independently executed against a live node; based on static code analysis of the read/write paths above. A concrete PoC would require: deploying a contract that within one transaction (1) calls the `freezeBalanceV2` native precompile/contract path to add weight, (2) calls `delegateResource` while the distorted `TotalNetWeight`/`TotalEnergyWeight` is in effect, and (3) calls `unfreezeBalanceV2` to revert the weight shift — then observing whether the delegated amount exceeds what the pre/post steady-state ratio would have allowed. This last verification step was not performed due to lack of a runtime/test environment in this analysis.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L60-72)
```java
    switch (freezeBalanceV2Contract.getResource()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(frozenBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        dynamicStore.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(frozenBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        dynamicStore.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L178-204)
```java
  public void updateTotalResourceWeight(AccountCapsule accountCapsule,
                                        Common.ResourceCode freezeType,
                                        long unfreezeBalance,
                                        Repository repo) {
    switch (freezeType) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(-unfreezeBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        repo.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(-unfreezeBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        repo.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(-unfreezeBalance);
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        repo.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
        //this should never happen
        break;
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L58-93)
```java
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
```
