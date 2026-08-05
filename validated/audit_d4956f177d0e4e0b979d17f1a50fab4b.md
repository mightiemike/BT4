## Title
Rounding-down of `transferUsage` in `UnDelegateResourceActuator`/`UnDelegateResourceProcessor` lets an owner reclaim delegated TRX while stripping away the associated bandwidth/energy usage debt — ([File: actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java])

### Summary
The reported bug class ("splitting a position moves the share/asset but the accompanying debt/liability rounds down to 0, so the debt silently disappears") has a direct analog in java-tron's resource-delegation model. When an owner un-delegates part of a previously delegated frozen balance from a receiver, the contract computes `transferUsage` — the portion of the receiver's bandwidth/energy usage debt that must be transferred back to the owner along with the reclaimed balance. This value is computed with floating-point double arithmetic and truncated to a `long`, so for small `unDelegateBalance` relative to the receiver's total delegated balance, `transferUsage` rounds down to `0`. When that happens, the code path that would merge the usage/window accounting (`processor.unDelegateIncrease(...)`) is skipped entirely (gated by `transferUsage > 0`), so the reclaimed TRX is returned to the owner's own frozen balance with **no accompanying usage debt**, while the receiver's `netUsage`/`energyUsage` is left completely untouched.

### Finding Description
In `UnDelegateResourceActuator.execute` (and its TVM-native counterpart `UnDelegateResourceProcessor.execute`), the usage to move back to the owner is computed as: [1](#0-0) 

and symmetrically for ENERGY: [2](#0-1) 

`transferUsage` is a `double` multiplication `receiverCapsule.getNetUsage() * (unDelegateBalance / receiverCapsule.getAllFrozenBalanceForBandwidth())` truncated via a `(long)` cast, which floors toward zero. When `unDelegateBalance` is a small fraction of the receiver's total delegated+acquired balance (`getAllFrozenBalanceForBandwidth()`), this product can be less than `1.0` and rounds down to `0`.

Later, in the "modify owner Account" block, the usage-window merge is only performed if `transferUsage > 0`: [3](#0-2) 

If `transferUsage == 0`, `processor.unDelegateIncrease(...)` — the routine that merges the receiver's consumption-weighted window size back into the owner's account (see `ResourceProcessor.unDelegateIncreaseV2` at `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java:222-260`) — is never invoked. Yet the owner's frozen balance is unconditionally increased: [4](#0-3) 

and the receiver's `netUsage`/`energyUsage` is left unchanged regardless of whether `transferUsage` was successfully computed: [5](#0-4) 

The identical logic (and identical rounding weakness) exists in the TVM native contract path used by smart contracts: [6](#0-5) [7](#0-6) 

Elsewhere in the codebase (e.g. `RepositoryImpl.getUsage`/`calculateGlobalEnergyLimit`, and `ResourceProcessor.unDelegateIncreaseV2`/`getNewWindowSize`) the same class of proportional calculations was hardened using `BigInteger` arithmetic behind `hardenResourceCalculation()`/`hardenCalculation()` flags to avoid exactly this kind of precision loss: [8](#0-7) 

But the `transferUsage` computation in `UnDelegateResourceActuator`/`UnDelegateResourceProcessor` was never migrated to this hardened path — it still uses raw `double` math, making it uniquely vulnerable to the rounding-to-zero bug class.

### Impact Explanation
An account owner who delegates frozen TRX to any receiver (including one they control) can repeatedly call `UnDelegateResourceContract` (or the TVM `unDelegateResourceAction`) with small `unDelegateBalance` chunks. Each chunk is chosen so `transferUsage` rounds to `0` (i.e. `unDelegateBalance` small relative to `receiverCapsule.getAllFrozenBalanceForBandwidth/Energy()`), so:
- The receiver's consumption-based usage (`netUsage`/`energyUsage`) is never reduced despite losing the corresponding backing balance.
- The owner regains the un-delegated TRX into their own `frozenBalanceForBandwidthV2`/`frozenBalanceForEnergyV2` capacity **without any of the associated usage/window merge** that would normally accompany a capacity transfer (`unDelegateIncrease`/`unDelegateIncreaseV2`).

By chunking a full un-delegation into many sub-threshold pieces, an attacker can fully reclaim delegated TRX while completely avoiding the usage-debt transfer that the protocol's accounting model otherwise enforces, effectively obtaining bandwidth/energy capacity "for free" — unbacked by the consumption history that the network's free-resource model is designed to track. This is an accounting-divergence bug directly analogous to H-03: shares (frozen balance) move, but the associated liability (usage debt) is dropped due to rounding.

### Likelihood Explanation
The attack requires only routine, unprivileged use of `UnDelegateResourceContract` (or its TVM equivalent), with attacker-chosen `unDelegateBalance` values and the freedom to split a large delegation into arbitrarily many small transactions. No special permissions, timing races, or privileged roles are required — only an initial `DelegateResource` and subsequent `UnDelegateResource` calls that the actuator's own `validate()` permits (it only checks `unDelegateBalance > 0` and sufficient delegated balance): [9](#0-8) 

The rounding-to-zero condition is easy to hit deterministically by controlling the ratio of `unDelegateBalance` to the receiver's `AllFrozenBalanceForBandwidth/Energy`.

### Recommendation
Compute `transferUsage` using integer/`BigInteger` arithmetic consistent with the hardened paths already used elsewhere (`RepositoryImpl.getUsage`, `ResourceProcessor.unDelegateIncreaseV2`/`getNewWindowSize`), and/or round the result up (ceiling) rather than truncating toward zero so that a non-zero usage transfer always occurs when balance is being returned. Additionally, ensure that when `transferUsage` rounds to `0` but `unDelegateBalance > 0`, the receiver's remaining `netUsage`/`energyUsage` is still consistently adjusted (or the owner's newly-returned balance is not granted "clean" capacity) so that usage accounting cannot be bypassed via chunked un-delegation.

### Proof of Concept
1. Owner delegates a large balance `B` (bandwidth or energy) to Receiver via `DelegateResourceContract`.
2. Receiver consumes bandwidth/energy so that `receiverCapsule.getNetUsage()` (or `getEnergyUsage()`) becomes non-trivial relative to `B`.
3. Owner calls `UnDelegateResourceContract` repeatedly with `unDelegateBalance = k` where `k` is chosen such that:
   `receiverCapsule.getNetUsage() * (k / receiverCapsule.getAllFrozenBalanceForBandwidth()) < 1`
   (e.g., `k` a very small fraction of the receiver's total delegated balance).
4. For each such call, per `UnDelegateResourceActuator.execute` (lines 80-92), `transferUsage` evaluates to `0`, so:
   - `receiverCapsule.getNetUsage()` is left unchanged (line 90-91: `newNetUsage = getNetUsage() - 0`).
   - `processor.unDelegateIncrease(...)` is never called (line 145: `transferUsage > 0` is false), so the owner's window/usage is not merged with any consumption debt.
   - The owner's `frozenBalanceForBandwidthV2` still increases by `k` (line 140).
5. Repeating step 3-4 until the full `B` has been un-delegated returns all TRX to the owner's own frozen balance with zero usage transferred at any point — a full reclaim of capacity with none of the corresponding usage debt.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L80-92)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L103-115)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L133-150)
```java
    // modify owner Account
    AccountCapsule ownerCapsule = accountStore.get(ownerAddress);
    switch (unDelegateResourceContract.getResource()) {
      case BANDWIDTH: {
        unlockResource.addFrozenBalanceForBandwidth(-unDelegateBalance, 0);

        ownerCapsule.addDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
        ownerCapsule.addFrozenBalanceForBandwidthV2(unDelegateBalance);

        BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);

        long now = chainBaseManager.getHeadSlot();
        if (Objects.nonNull(receiverCapsule) && transferUsage > 0) {
          processor.unDelegateIncrease(ownerCapsule, receiverCapsule,
              transferUsage, BANDWIDTH, now);
        }
      }
      break;
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L265-268)
```java
    long unDelegateBalance = unDelegateResourceContract.getBalance();
    if (unDelegateBalance <= 0) {
      throw new ContractValidateException("unDelegateBalance must be more than 0 TRX");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L114-126)
```java
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * dynamicStore.getTotalNetLimit() / repo.getTotalNetWeight());
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage, VMConfig.disableJavaLangMath());

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }

          long newNetUsage = receiverCapsule.getNetUsage() - transferUsage;
          receiverCapsule.setNetUsage(newNetUsage);
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L160-175)
```java
    // modify owner Account
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, false);
    DelegatedResourceCapsule delegatedResourceCapsule = repo.getDelegatedResource(key);
    switch (param.getResourceType()) {
      case BANDWIDTH: {
        delegatedResourceCapsule.addFrozenBalanceForBandwidth(-unDelegateBalance, 0);

        ownerCapsule.addDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
        ownerCapsule.addFrozenBalanceForBandwidthV2(unDelegateBalance);

        BandwidthProcessor processor = new BandwidthProcessor(ChainBaseManager.getInstance());
        if (Objects.nonNull(receiverCapsule) && transferUsage > 0) {
          processor.unDelegateIncrease(ownerCapsule, receiverCapsule,
              transferUsage, BANDWIDTH, now);
        }
      }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L244-254)
```java
    if (hardenCalculation()) {
      BigInteger bi = BigInteger.valueOf(ownerUsage)
          .multiply(BigInteger.valueOf(remainOwnerWindowSizeV2))
          .add(BigInteger.valueOf(transferUsage)
              .multiply(BigInteger.valueOf(remainReceiverWindowSizeV2)));
      newOwnerWindowSize = divideCeilExact(bi, BigInteger.valueOf(newOwnerUsage));
    } else {
      newOwnerWindowSize = divideCeil(
          ownerUsage * remainOwnerWindowSizeV2 + transferUsage * remainReceiverWindowSizeV2,
          newOwnerUsage);
    }
```
