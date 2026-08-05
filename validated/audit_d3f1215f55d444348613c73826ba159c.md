Based on my research, I found a concrete, unprivileged-user-reachable rounding issue in java-tron's resource delegation flow that mirrors the Zivoe `applyCombine` APR-truncation bug: a floor-rounded, floating-point-based proportional calculation that a user can manipulate by splitting a single action into multiple smaller ones to shift the truncation loss in their own favor. Unlike the hardened, ceiling-based `divideCeilExact` calculations elsewhere in `ResourceProcessor`/`RepositoryImpl` (which conservatively round up to protect the network), the `transferUsage`/`unDelegateMaxUsage` computation in `UnDelegateResourceActuator` (and its TVM analog) is a plain floor-truncated double computation with no such protection.

### Title
Floor-rounded `transferUsage` calculation in `UnDelegateResourceActuator`/`DelegateResourceProcessor` lets a user split un-delegations to under-report consumed resource usage - (File: actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java)

### Summary
`UnDelegateResourceActuator.execute()` computes how much bandwidth/energy "usage" must be transferred back from a resource receiver to the resource owner when delegated TRX is un-delegated, via `transferUsage = (long)(receiverCapsule.getNetUsage() * ((double)(unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));`, capped by `unDelegateMaxUsage`, both computed with a `double`-based multiplication cast down to `long` (floor truncation), exactly mirroring the `APR = APR / notional` truncation in the Zivoe report. [1](#0-0)  Because this floor happens per-call, an owner can split a single large un-delegation into many small un-delegation transactions, each losing a fraction of `transferUsage` to truncation, so cumulatively less "usage" is added back to the owner's account than a single un-delegation of the same total amount would produce.

### Finding Description
The bandwidth/energy resource usage window in java-tron elsewhere is carefully hardened with `BigInteger`-based ceiling division (`divideCeilExact`) precisely to avoid systematic rounding errors that could be gamed, as seen throughout `ResourceProcessor`: [2](#0-1)  and `RepositoryImpl`: [3](#0-2) .

In contrast, `UnDelegateResourceActuator.execute()` computes the usage to be moved from receiver to owner using an un-hardened `double` calculation truncated via `(long)` cast (floor), for both bandwidth and energy resources: [4](#0-3) . The identical unprotected pattern also exists in the TVM-callable path `UnfreezeBalanceV2Processor`-style resource un-delegation logic in `FreezeV2Util.checkUndelegateResource`: [5](#0-4) .

This is the direct structural analog of the Sherlock finding: a weighted-proportion calculation (`userVote/totalVote`-style ratio, here `unDelegateBalance/allFrozenBalance`) multiplied by a base quantity (`totalReward`, here `receiverCapsule.getNetUsage()`), where the division/multiplication is always truncated toward zero. As in the Zivoe case, the amount an unprivileged user (the resource owner, who fully controls how they call `UnDelegateResourceActuator`, an ordinary user-facing contract type) loses to truncation *per call* can be repeated by splitting one large operation into N smaller ones — each of the N calls independently floors its own `transferUsage`, so the sum of N floors is ≤ the floor of one combined operation. The owner therefore ends up crediting themselves with strictly less `netUsage`/`energyUsage` debit than they legitimately consumed via delegation, retaining an artificially larger free resource allowance going forward.

### Impact Explanation
This is an "invalid-state/divergence"-style accounting bug affecting the network's bandwidth/energy resource ledger, the TRON equivalent of "underpriced public work": the resource usage windows (`NetUsage`/`EnergyUsage`) are what gate free bandwidth/energy consumption for every account. Systematically under-crediting usage lets an account retain more available free bandwidth/energy than its actual consumption should permit, at the expense of the shared resource pool (`TotalNetLimit`/`TotalEnergyLimit`) other network participants rely on — directly analogous to the borrower shaving APR off a loan at the lender/protocol's expense in the referenced report.

### Likelihood Explanation
The exploit requires no privileged role: any account that has delegated resources to another can freely choose to un-delegate in many small increments instead of one large transaction, exactly as the Zivoe borrower could trigger extra loan repayments to force the rounding direction. The per-call gain is small (bounded by fractional truncation of a `double` multiplication), but it is fully deterministic and repeatable at zero cost beyond ordinary transaction fees, so with enough repetitions the accumulated free-resource skew becomes non-negligible — mirroring the "low precision, minor per-action but non-negligible cumulative loss" reasoning that the Sherlock judges ultimately accepted as valid Medium severity.

### Recommendation
Replace the unhardened `double`-based floor truncation in `UnDelegateResourceActuator.execute()` (and the equivalent `FreezeV2Util.checkUndelegateResource`) with the same `BigInteger`-based, exact/ceiling-rounded arithmetic already used elsewhere in `ResourceProcessor`/`RepositoryImpl` (`divideCeilExact`), or otherwise ensure the transferred usage sums consistently regardless of how many partial un-delegation calls are used to reach the same total `unDelegateBalance`, e.g. by tracking/accruing the truncated remainder instead of discarding it on each call.

### Proof of Concept
1. Owner delegates a large bandwidth balance to Receiver; Receiver accrues `NetUsage`.
2. Owner calls `UnDelegateResourceActuator` once for the full `unDelegateBalance`: `transferUsage_full = floor(receiverNetUsage * unDelegateBalance / allFrozenBalance)`.
3. Instead, Owner splits the same `unDelegateBalance` into `k` equal calls of `unDelegateBalance/k` each. For each call, `transferUsage_i = floor(receiverNetUsage_i * (unDelegateBalance/k) / allFrozenBalance_i)`.
4. Due to floor truncation being applied independently `k` times (each losing up to just-under-1 unit), `Σ transferUsage_i ≤ transferUsage_full`, with the gap growing with `k`.
5. Owner's resulting `NetUsage` after the split un-delegations is strictly lower than after the single full un-delegation, giving Owner more free bandwidth headroom than legitimately earned, at the network's expense.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L80-116)
```java
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalNetLimit()) / dynamicStore.getTotalNetWeight()));
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }

          long newNetUsage = receiverCapsule.getNetUsage() - transferUsage;
          receiverCapsule.setNetUsage(newNetUsage);
          receiverCapsule.setLatestConsumeTime(now);
          break;
        case ENERGY:
          EnergyProcessor energyProcessor = new EnergyProcessor(dynamicStore, accountStore);
          energyProcessor.updateUsage(receiverCapsule);

          if (receiverCapsule.getAcquiredDelegatedFrozenV2BalanceForEnergy()
              < unDelegateBalance) {
            // A TVM contract receiver, re-create will produce this situation
            receiverCapsule.setAcquiredDelegatedFrozenV2BalanceForEnergy(0);
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalEnergyCurrentLimit()) / dynamicStore.getTotalEnergyWeight()));
            transferUsage = (long) (receiverCapsule.getEnergyUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForEnergy()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(-unDelegateBalance);
          }

          long newEnergyUsage = receiverCapsule.getEnergyUsage() - transferUsage;
          receiverCapsule.setEnergyUsage(newEnergyUsage);
          receiverCapsule.setLatestConsumeTimeForEnergy(now);
          break;
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L272-283)
```java
  private long divideCeil(long numerator, long denominator) {
    return (numerator / denominator) + ((numerator % denominator) > 0 ? 1 : 0);
  }

  private long divideCeilExact(BigInteger numerator, BigInteger denominator) {
    BigInteger[] divRem = numerator.divideAndRemainder(denominator);
    long result = divRem[0].longValueExact();
    if (divRem[1].signum() > 0) {
      result = StrictMathWrapper.addExact(result, 1);
    }
    return result;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L940-951)
```java
  private long divideCeil(long numerator, long denominator) {
    return (numerator / denominator) + ((numerator % denominator) > 0 ? 1 : 0);
  }

  private long divideCeilExact(BigInteger numerator, BigInteger denominator) {
    BigInteger[] divRem = numerator.divideAndRemainder(denominator);
    long result = divRem[0].longValueExact();
    if (divRem[1].signum() > 0) {
      result = StrictMathWrapper.addExact(result, 1);
    }
    return result;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java (L225-232)
```java
    amount = min(amount, resourceLimit, VMConfig.disableJavaLangMath());
    if (resourceLimit <= usagePair.getLeft()) {
      return Triple.of(0L, amount, usagePair.getRight());
    }

    long clean = (long) (amount * ((double) (resourceLimit - usagePair.getLeft()) / resourceLimit));

    return Triple.of(clean, amount - clean, usagePair.getRight());
```
