### Title
Personal resource window manipulation via repeated Delegate/UnDelegate can distort bandwidth/energy accounting windows, mirroring the buy/sell period asymmetry bug — ([File: chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java])

### Summary
The reported `dpnm_sc.sol` issue works because the contract tracks a rolling "buy" limit and a rolling "sell" cool-down with **different window lengths**, so a user can reset the buy-window accounting by performing many buy/sell round-trips and then execute a large buy that the naive 48h/24h bookkeeping fails to cap correctly. The structurally equivalent pattern in java-tron is the **per-account resource window** (`windowSize`/`windowSizeV2`) used by the bandwidth/energy accounting algorithm, which is recomputed as a *usage-weighted average of the old window and a newly-supplied window* every time resource is delegated or un-delegated. Because this weighted average can be driven by an attacker-controlled sequence of `DelegateResource`/`UnDelegateResource` operations (each of which is an ordinary, unprivileged broadcast transaction), the account's effective decay window can be pushed away from the canonical `windowSize` (24h in slots), causing the "recovered" usage calculation to diverge from what the protocol intends — the same fundamental primitive (mismatched/attacker-influenced time windows feeding a shared limit calculation) as the reported bug.

### Finding Description
`ResourceProcessor.increase()` computes a decayed usage figure using a `windowSize` argument, and `increaseV2()` (used once `supportAllowCancelAllUnfreezeV2()` is enabled) additionally recomputes a **new personal window size** as a weighted average of the caller's existing remaining window and the resource windows involved in the transaction: [1](#0-0) 

The weighting inputs (`remainUsage`, `remainWindowSize`, `usage`, `this.windowSize`) are all derived from values that a user directly controls by choosing *when* and *how much* to delegate or un-delegate resource — analogous to the report's attacker choosing when to buy/sell within the mismatched 24h/48h windows: [2](#0-1) 

This per-account `windowSizeV2` is then fed back into `increase()`/`recovery()` on every subsequent bandwidth/energy consumption to determine how quickly usage "decays" (i.e., how much resource capacity is considered recovered): [3](#0-2) [4](#0-3) 

Both `DelegateResourceActuator` and `UnDelegateResourceActuator` are ordinary contract actuators reachable from any broadcast transaction, and they directly manipulate the frozen/delegated balances and windows that feed this calculation: [5](#0-4) [6](#0-5) 

Just as the reported bug exploits the fact that the sell-cooldown window (48h) is longer/misaligned with the buy-limit window (24h), here the "old window" carried on the account (`oldWindowSizeV2`) and the "new window" supplied by the current operation (`this.windowSize`) can be repeatedly and asymmetrically combined through many small delegate/undelegate operations, nudging the effective decay window used for bandwidth/energy accounting away from the canonical value the protocol assumes for its daily resource limit.

### Impact Explanation
If an attacker can drive their personal `windowSizeV2` down through repeated cheap delegate/undelegate round-trips, their bandwidth/energy usage will be reported as decaying (recovering) faster than the protocol intends, letting them consume more bandwidth/energy from the shared `TotalNetLimit`/`TotalEnergyCurrentLimit` pool than their frozen/staked weight entitles them to — an accounting corruption that degrades fairness of the shared resource pool and can be used as a resource-exhaustion vector against other network participants (DoS-adjacent), similar to how the reported bug lets an attacker exceed a daily buy cap and move price against a victim buyer.

### Likelihood Explanation
The primitive requires only unprivileged, low-cost transactions (`FreezeBalanceV2Contract`, `DelegateResourceContract`, `UnDelegateResourceContract`) that any account can send, and the weighting formula in `increaseV2`/`getNewWindowSize` is fully attacker-influenced (usage and window values chosen by the attacker's own transaction pattern), so the mechanics are directly reachable. However, unlike the original report (which had a concrete numeric example proving the exploit profitable net of fees), this analog requires further arithmetic modeling to prove the net window drift is exploitable to a degree that produces a materially unfair resource advantage after transaction fees/lock periods are accounted for — this reduces confidence relative to the original finding.

### Recommendation
Bound the rate/degree at which a single account's `windowSizeV2` can be moved per unit of wall-clock time (e.g., clamp the maximum window-size delta per block/day regardless of how many delegate/undelegate operations occur), and/or make the weighted-average formula less sensitive to a rapid sequence of small operations (e.g., require a minimum elapsed time or minimum transferred amount between window-size recalculations), mirroring the report's fix of tightening the mismatched window rather than relying solely on transaction fees as a deterrent.

### Proof of Concept
1. Freeze balance for BANDWIDTH/ENERGY (`FreezeBalanceV2Contract`) and delegate a portion with `lock=true`/various `lockPeriod`s to a second account the attacker also controls, driving `DelegatedResourceCapsule` window fields via `DelegateResourceActuator.delegateResource()` [7](#0-6) .
2. Immediately un-delegate via `UnDelegateResourceActuator`, which invokes `ResourceProcessor.unDelegateIncreaseV2` and recalculates `newOwnerWindowSize` as a weighted average based on `remainOwnerWindowSizeV2`/`remainReceiverWindowSizeV2` [8](#0-7) .
3. Repeat steps 1–2 in a tight loop with carefully chosen small `balance`/`lockPeriod` values to iteratively pull `windowSizeV2` toward a value smaller than the canonical `windowSize`.
4. Observe, via `Wallet.getAccountNetUsageBalanceAndRestoreSeconds`/`getAccountEnergyUsageBalanceAndRestoreSeconds`, that the reported "restoreSlots"/recovered balance for the account decays faster than an account that never manipulated its window, confirming the divergence in accounted resource usage [9](#0-8) .

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L133-188)
```java
  public long increaseV2(AccountCapsule accountCapsule, ResourceCode resourceCode,
      long lastUsage, long usage, long lastTime, long now) {
    long oldWindowSizeV2 = accountCapsule.getWindowSizeV2(resourceCode);
    long oldWindowSize = accountCapsule.getWindowSize(resourceCode);
    long averageLastUsage;
    long averageUsage;
    if (hardenCalculation()) {
      BigInteger biPrecision = BigInteger.valueOf(this.precision);
      averageLastUsage = divideCeilExact(
          BigInteger.valueOf(lastUsage).multiply(biPrecision),
          BigInteger.valueOf(oldWindowSize));
      averageUsage = divideCeilExact(
          BigInteger.valueOf(usage).multiply(biPrecision),
          BigInteger.valueOf(this.windowSize));
    } else {
      averageLastUsage = divideCeil(lastUsage * this.precision, oldWindowSize);
      averageUsage = divideCeil(usage * this.precision, this.windowSize);
    }

    if (lastTime != now) {
      if (lastTime + oldWindowSize > now) {
        long delta = now - lastTime;
        double decay = (oldWindowSize - delta) / (double) oldWindowSize;
        averageLastUsage = round(averageLastUsage * decay,
            this.disableJavaLangMath());
      } else {
        averageLastUsage = 0;
      }
    }

    long newUsage = getUsage(averageLastUsage, oldWindowSize, averageUsage, this.windowSize);
    long remainUsage = getUsage(averageLastUsage, oldWindowSize);
    if (remainUsage == 0) {
      accountCapsule.setNewWindowSizeV2(resourceCode, this.windowSize * WINDOW_SIZE_PRECISION);
      return newUsage;
    }

    long remainWindowSize = oldWindowSizeV2 - (now - lastTime) * WINDOW_SIZE_PRECISION;
    long newWindowSize;
    if (hardenCalculation()) {
      BigInteger biNewWindowSize = BigInteger.valueOf(remainUsage)
          .multiply(BigInteger.valueOf(remainWindowSize))
          .add(BigInteger.valueOf(usage)
              .multiply(BigInteger.valueOf(this.windowSize))
              .multiply(BigInteger.valueOf(WINDOW_SIZE_PRECISION)));
      newWindowSize = divideCeilExact(biNewWindowSize, BigInteger.valueOf(newUsage));
    } else {
      newWindowSize = divideCeil(
          remainUsage * remainWindowSize + usage * this.windowSize * WINDOW_SIZE_PRECISION,
          newUsage);
    }
    newWindowSize = min(newWindowSize, this.windowSize * WINDOW_SIZE_PRECISION,
        this.disableJavaLangMath());
    accountCapsule.setNewWindowSizeV2(resourceCode, newWindowSize);
    return newUsage;
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L190-260)
```java
  public void unDelegateIncrease(AccountCapsule owner, final AccountCapsule receiver,
      long transferUsage, ResourceCode resourceCode, long now) {
    if (dynamicPropertiesStore.supportAllowCancelAllUnfreezeV2()) {
      unDelegateIncreaseV2(owner, receiver, transferUsage, resourceCode, now);
      return;
    }
    long lastOwnerTime = owner.getLastConsumeTime(resourceCode);
    long ownerUsage = owner.getUsage(resourceCode);
    // Update itself first
    ownerUsage = increase(owner, resourceCode, ownerUsage, 0, lastOwnerTime, now);

    long remainOwnerWindowSize = owner.getWindowSize(resourceCode);
    long remainReceiverWindowSize = receiver.getWindowSize(resourceCode);
    remainOwnerWindowSize = remainOwnerWindowSize < 0 ? 0 : remainOwnerWindowSize;
    remainReceiverWindowSize = remainReceiverWindowSize < 0 ? 0 : remainReceiverWindowSize;

    long newOwnerUsage = ownerUsage + transferUsage;
    // mean ownerUsage == 0 and transferUsage == 0
    if (newOwnerUsage == 0) {
      owner.setNewWindowSize(resourceCode, this.windowSize);
      owner.setUsage(resourceCode, 0);
      owner.setLatestTime(resourceCode, now);
      return;
    }
    // calculate new windowSize
    long newOwnerWindowSize = getNewWindowSize(ownerUsage, remainOwnerWindowSize, transferUsage,
        remainReceiverWindowSize, newOwnerUsage);
    owner.setNewWindowSize(resourceCode, newOwnerWindowSize);
    owner.setUsage(resourceCode, newOwnerUsage);
    owner.setLatestTime(resourceCode, now);
  }

  public void unDelegateIncreaseV2(AccountCapsule owner, final AccountCapsule receiver,
      long transferUsage, ResourceCode resourceCode, long now) {
    long lastOwnerTime = owner.getLastConsumeTime(resourceCode);
    long ownerUsage = owner.getUsage(resourceCode);
    // Update itself first
    ownerUsage = increase(owner, resourceCode, ownerUsage, 0, lastOwnerTime, now);
    long newOwnerUsage = ownerUsage + transferUsage;
    // mean ownerUsage == 0 and transferUsage == 0
    if (newOwnerUsage == 0) {
      owner.setNewWindowSizeV2(resourceCode, this.windowSize * WINDOW_SIZE_PRECISION);
      owner.setUsage(resourceCode, 0);
      owner.setLatestTime(resourceCode, now);
      return;
    }

    long remainOwnerWindowSizeV2 = owner.getWindowSizeV2(resourceCode);
    long remainReceiverWindowSizeV2 = receiver.getWindowSizeV2(resourceCode);
    remainOwnerWindowSizeV2 = remainOwnerWindowSizeV2 < 0 ? 0 : remainOwnerWindowSizeV2;
    remainReceiverWindowSizeV2 = remainReceiverWindowSizeV2 < 0 ? 0 : remainReceiverWindowSizeV2;

    // calculate new windowSize
    long newOwnerWindowSize;
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
    newOwnerWindowSize = min(newOwnerWindowSize, this.windowSize * WINDOW_SIZE_PRECISION,
        this.disableJavaLangMath());
    owner.setNewWindowSizeV2(resourceCode, newOwnerWindowSize);
    owner.setUsage(resourceCode, newOwnerUsage);
    owner.setLatestTime(resourceCode, now);
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L468-504)
```java
  private boolean useAccountNet(AccountCapsule accountCapsule, long bytes, long now) {

    long netUsage = accountCapsule.getNetUsage();
    long latestConsumeTime = accountCapsule.getLatestConsumeTime();
    long netLimit = calculateGlobalNetLimit(accountCapsule);

    long newNetUsage;
    if (!dynamicPropertiesStore.supportUnfreezeDelay()) {
      newNetUsage = increase(netUsage, 0, latestConsumeTime, now);
    } else {
      // only participate in the calculation as a temporary variable, without disk flushing
      newNetUsage = recovery(accountCapsule, BANDWIDTH, netUsage, latestConsumeTime, now);
    }


    if (bytes > (netLimit - newNetUsage)) {
      logger.debug("Net usage is running out, now use free net usage."
              + " Bytes: {}, netLimit: {}, newNetUsage: {}.",
          bytes, netLimit, newNetUsage);
      return false;
    }

    long latestOperationTime = chainBaseManager.getHeadBlockTimeStamp();
    if (!dynamicPropertiesStore.supportUnfreezeDelay()) {
      newNetUsage = increase(newNetUsage, bytes, now, now);
    } else {
      // Participate in calculation and flush disk persistence
      newNetUsage = increase(accountCapsule, BANDWIDTH, netUsage, bytes, latestConsumeTime, now);
    }

    accountCapsule.setNetUsage(newNetUsage);
    accountCapsule.setLatestOperationTime(latestOperationTime);
    accountCapsule.setLatestConsumeTime(now);

    chainBaseManager.getAccountStore().put(accountCapsule.createDbKey(), accountCapsule);
    return true;
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L102-143)
```java
  public boolean useEnergy(AccountCapsule accountCapsule, long energy, long now) {

    long energyUsage = accountCapsule.getEnergyUsage();
    long latestConsumeTime = accountCapsule.getAccountResource().getLatestConsumeTimeForEnergy();
    long energyLimit = calculateGlobalEnergyLimit(accountCapsule);
    long newEnergyUsage;
    if (!dynamicPropertiesStore.supportUnfreezeDelay()) {
      newEnergyUsage = increase(energyUsage, 0, latestConsumeTime, now);
    } else {
      // only participate in the calculation as a temporary variable, without disk flushing
      newEnergyUsage = recovery(accountCapsule, ENERGY, energyUsage,
          latestConsumeTime, now);
    }

    if (energy > (energyLimit - newEnergyUsage)
        && dynamicPropertiesStore.getAllowTvmFreeze() == 0
        && !dynamicPropertiesStore.supportUnfreezeDelay()) {
      return false;
    }

    long latestOperationTime = dynamicPropertiesStore.getLatestBlockHeaderTimestamp();
    if (!dynamicPropertiesStore.supportUnfreezeDelay()) {
      newEnergyUsage = increase(newEnergyUsage, energy, now, now);
    } else {
      // Participate in calculation and flush disk persistence
      newEnergyUsage = increase(accountCapsule, ENERGY, energyUsage, energy,
          latestConsumeTime, now);
    }

    accountCapsule.setEnergyUsage(newEnergyUsage);
    accountCapsule.setLatestOperationTime(latestOperationTime);
    accountCapsule.setLatestConsumeTimeForEnergy(now);

    accountStore.put(accountCapsule.createDbKey(), accountCapsule);

    if (dynamicPropertiesStore.getAllowAdaptiveEnergy() == 1) {
      long blockEnergyUsage = dynamicPropertiesStore.getBlockEnergyUsage() + energy;
      dynamicPropertiesStore.saveBlockEnergyUsage(blockEnergyUsage);
    }

    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L282-325)
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

    //modify DelegatedResourceAccountIndexStore
    delegatedResourceAccountIndexStore.delegateV2(ownerAddress, receiverAddress,
        dynamicPropertiesStore.getLatestBlockHeaderTimestamp());

    //modify AccountStore for receiver
    AccountCapsule receiverCapsule = accountStore.get(receiverAddress);
    if (isBandwidth) {
      receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(balance);
    } else {
      receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(balance);
    }
    accountStore.put(receiverCapsule.createDbKey(), receiverCapsule);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L255-304)
```java
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, false);
    DelegatedResourceCapsule unlockResourceCapsule = delegatedResourceStore.get(key);
    byte[] lockKey = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, true);
    DelegatedResourceCapsule lockResourceCapsule = delegatedResourceStore.get(lockKey);
    if (unlockResourceCapsule == null && lockResourceCapsule == null) {
      throw new ContractValidateException(
          "delegated Resource does not exist");
    }

    long unDelegateBalance = unDelegateResourceContract.getBalance();
    if (unDelegateBalance <= 0) {
      throw new ContractValidateException("unDelegateBalance must be more than 0 TRX");
    }
    switch (unDelegateResourceContract.getResource()) {
      case BANDWIDTH: {
        long delegateBalance = 0;
        if (unlockResourceCapsule != null) {
          delegateBalance += unlockResourceCapsule.getFrozenBalanceForBandwidth();
        }
        if (lockResourceCapsule != null
            && lockResourceCapsule.getExpireTimeForBandwidth() < now) {
          delegateBalance += lockResourceCapsule.getFrozenBalanceForBandwidth();
        }
        if (delegateBalance < unDelegateBalance) {
          throw new ContractValidateException(
              "insufficient delegatedFrozenBalance(BANDWIDTH), request="
                  + unDelegateBalance + ", unlock_balance=" + delegateBalance);
        }
      }
      break;
      case ENERGY: {
        long delegateBalance = 0;
        if (unlockResourceCapsule != null) {
          delegateBalance += unlockResourceCapsule.getFrozenBalanceForEnergy();
        }
        if (lockResourceCapsule != null
            && lockResourceCapsule.getExpireTimeForEnergy() < now) {
          delegateBalance += lockResourceCapsule.getFrozenBalanceForEnergy();
        }
        if (delegateBalance < unDelegateBalance) {
          throw new ContractValidateException("insufficient delegateFrozenBalance(Energy), request="
              + unDelegateBalance + ", unlock_balance=" + delegateBalance);
        }
      }
      break;
      default:
        throw new ContractValidateException(
            "ResourceCode error.valid ResourceCode[BANDWIDTH、Energy]");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L210-254)
```java
  public Pair<Long, Long> getAccountEnergyUsageBalanceAndRestoreSeconds(AccountCapsule accountCapsule) {
    long now = getHeadSlot();

    long energyUsage = accountCapsule.getEnergyUsage();
    long latestConsumeTime = accountCapsule.getAccountResource().getLatestConsumeTimeForEnergy();
    long accountWindowSize = accountCapsule.getWindowSize(Common.ResourceCode.ENERGY);

    if (now >= latestConsumeTime + accountWindowSize) {
      return Pair.of(0L, 0L);
    }

    long restoreSlots = latestConsumeTime + accountWindowSize - now;

    long newEnergyUsage = recover(energyUsage, latestConsumeTime, now, accountWindowSize);

    long totalEnergyLimit = getDynamicPropertiesStore().getTotalEnergyCurrentLimit();
    long totalEnergyWeight = getTotalEnergyWeight();

    long balance = usageToBalance(newEnergyUsage, totalEnergyWeight, totalEnergyLimit);

    return Pair.of(balance, restoreSlots * BLOCK_PRODUCED_INTERVAL / 1_000);
  }

  public Pair<Long, Long> getAccountNetUsageBalanceAndRestoreSeconds(AccountCapsule accountCapsule) {
    long now = getHeadSlot();

    long netUsage = accountCapsule.getNetUsage();
    long latestConsumeTime = accountCapsule.getLatestConsumeTime();
    long accountWindowSize = accountCapsule.getWindowSize(Common.ResourceCode.BANDWIDTH);

    if (now >= latestConsumeTime + accountWindowSize) {
      return Pair.of(0L, 0L);
    }

    long restoreSlots = latestConsumeTime + accountWindowSize - now;

    long newNetUsage = recover(netUsage, latestConsumeTime, now, accountWindowSize);

    long totalNetLimit = getDynamicPropertiesStore().getTotalNetLimit();
    long totalNetWeight = getTotalNetWeight();

    long balance = usageToBalance(newNetUsage, totalNetWeight, totalNetLimit);

    return Pair.of(balance, restoreSlots * BLOCK_PRODUCED_INTERVAL / 1_000);
  }
```
