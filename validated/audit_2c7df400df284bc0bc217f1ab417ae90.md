### Title
Griefing of the shared "free bandwidth" daily pool via unauthenticated broadcast transactions - (File: chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java)

### Summary
The dForce `TimeLockStrategy` finding describes a global daily-limit counter that any unprivileged caller can inflate through cheap, repeated calls, forcing the counter's shared state into an undesired condition and imposing inconvenience/cost on other legitimate users until the owner intervenes. java-tron has a structurally identical pattern in its bandwidth-accounting subsystem: a single, network-wide "one day" free-bandwidth pool (`PUBLIC_NET_LIMIT`/`PUBLIC_NET_USAGE`) that is shared by every account using the free-tier bandwidth quota, and that any anonymous broadcast transaction can consume.

### Finding Description
`BandwidthProcessor.useFreeNet` implements the free-bandwidth path used when an account has no bandwidth from freezing TRX. It checks and increments two counters:
1. `freeNetUsage` — a per-account 5000-byte/day quota (`FREE_NET_LIMIT`).
2. `publicNetUsage` — a single, chain-wide counter capped by `PUBLIC_NET_LIMIT` (default `14_400_000_000L`, described as the "one day" public pool, see `ONE_DAY_NET_LIMIT`/`PUBLIC_NET_LIMIT` in `DynamicPropertiesStore`). [1](#0-0) 

The global counter is accumulated with the same decaying-window `increase()` helper used for per-account quotas (`ResourceProcessor.increase`), where the counter resets/decays only as a function of elapsed time, not per-caller identity: [2](#0-1) 

Because `publicNetUsage`/`publicNetTime` are single global values stored in `DynamicPropertiesStore` (not per-account), and are incremented on *any* transaction from *any* account that falls into the free-net path (no frozen balance required, no fee, no signature-cost beyond a normal broadcast), an unprivileged attacker can create or use many low-cost/zero-balance accounts and rapidly broadcast minimal transactions to drive `publicNetUsage` up to `PUBLIC_NET_LIMIT` within the rolling window: [3](#0-2) [4](#0-3) 

Once exhausted, every other account on the network that relies on the free-bandwidth path (`bytes > (publicNetLimit - newPublicNetUsage)`) will have `useFreeNet` return `false`: [5](#0-4) 

and `BandwidthProcessor.consume` falls through to `useTransactionFee`, forcing legitimate free-tier senders to pay TRX fees, or to fail with `AccountResourceInsufficientException` if they lack balance: [6](#0-5) 

This mirrors the dForce bug class exactly: a single global daily accounting value, meant to bound aggregate usage, can be driven into an exhausted/"locked" state by any unprivileged party through repeated cheap calls, with the only remedy being a chain-parameter change by the witnesses/committee (equivalent to "owner intervention") via a `TotalNetLimit`/`PublicNetLimit` proposal.

### Impact Explanation
This is a network-wide denial-of-service/griefing vector against the free-bandwidth allowance: legitimate zero/low-balance accounts (which the free-tier mechanism specifically exists to serve) are forced to either pay unexpected TRX fees or have their transactions rejected for insufficient resources, until the shared window naturally decays or a witness proposal raises `PUBLIC_NET_LIMIT`. Because the "attack" only requires broadcasting ordinary, free (no fee, no frozen balance) transactions, the cost to the attacker is near zero relative to the network-wide disruption caused, and the effect impacts all users depending on `useFreeNet`, not just the attacker's own account.

### Likelihood Explanation
High likelihood of reachability: `useFreeNet`/`publicNetUsage` is invoked on the default free-net code path for any `TransferContract`/similar transaction from an account without frozen bandwidth, which is reachable by any anonymous broadcast transaction with no special privileges, signature requirements beyond normal, or balance. The `PUBLIC_NET_LIMIT` default (`14,400,000,000` byte-equivalents, i.e. `precision`-scaled units) bounds how much sustained traffic is needed, but is a fixed global constant that does not scale with the number of participating accounts, making exhaustion a matter of steady low-cost transaction volume from many free accounts within the rolling window.

### Recommendation
Rework the free-bandwidth accounting so the public/global daily pool cannot be trivially exhausted by a small number of unprivileged accounts:
- Consider per-account or per-IP-bounded contribution caps into the shared `publicNetUsage` pool (e.g., cap how much of the public pool any single account/address can consume per window), similar to how `FREE_NET_LIMIT` already caps individual free usage but does not currently limit an account's share of the *shared* pool beyond that per-account cap.
- Consider weighting `PUBLIC_NET_LIMIT` dynamically (adaptively, as is already done for energy via `updateAdaptiveTotalEnergyLimit`) so sustained abuse triggers throttling of the abuser rather than starving all other free-tier accounts.
- Add monitoring/alerting on `publicNetUsage` saturation velocity so witnesses can react before it fully impacts legitimate free-tier senders, since currently a raise of `PUBLIC_NET_LIMIT` via committee proposal is the only mitigation once exhausted.

### Proof of Concept
1. Create N low/zero-balance accounts (no frozen TRX required for free-net path).
2. From each account, broadcast ordinary `TransferContract`/`TransferAssetContract` transactions sized to `FREE_NET_LIMIT` (5000 bytes/day per account) — this is well within `useFreeNet`'s personal check.
3. Each transaction increments the single global `PUBLIC_NET_USAGE` value via `increase(publicNetUsage, 0, publicNetTime, now)` then `increase(newPublicNetUsage, bytes, publicNetTime, now)` in `useFreeNet` (lines 506–546 of `BandwidthProcessor.java`).
4. Repeating this across enough accounts within the rolling window (bounded by `PUBLIC_NET_LIMIT = 14_400_000_000L` and the internal precision/window-size scaling) drives `newPublicNetUsage` to the `publicNetLimit` ceiling.
5. Subsequent unrelated free-tier accounts attempting `useFreeNet` now fail the check `bytes > (publicNetLimit - newPublicNetUsage)` and are routed to `useTransactionFee`/rejected, denying them the intended free-bandwidth allowance until the shared window decays or `PUBLIC_NET_LIMIT` is raised by a witness proposal.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L160-177)
```java
      if (useAccountNet(accountCapsule, bytesSize, now)) {
        continue;
      }

      if (useFreeNet(accountCapsule, bytesSize, now)) {
        continue;
      }

      if (useTransactionFee(accountCapsule, bytesSize, trace)) {
        continue;
      }

      long fee = chainBaseManager.getDynamicPropertiesStore().getTransactionFee() * bytesSize;
      throw new AccountResourceInsufficientException(
          String.format(
              "account [%s] has insufficient bandwidth[%d] and balance[%d] to create new account",
              StringUtil.encode58Check(address), bytesSize, fee));
    }
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L506-546)
```java
  private boolean useFreeNet(AccountCapsule accountCapsule, long bytes, long now) {

    long freeNetLimit = chainBaseManager.getDynamicPropertiesStore().getFreeNetLimit();
    long freeNetUsage = accountCapsule.getFreeNetUsage();
    long latestConsumeFreeTime = accountCapsule.getLatestConsumeFreeTime();
    long newFreeNetUsage = increase(freeNetUsage, 0, latestConsumeFreeTime, now);

    if (bytes > (freeNetLimit - newFreeNetUsage)) {
      logger.debug("Free net usage is running out."
              + " Bytes: {}, freeNetLimit: {}, newFreeNetUsage: {}.",
          bytes, freeNetLimit, newFreeNetUsage);
      return false;
    }

    long publicNetLimit = chainBaseManager.getDynamicPropertiesStore().getPublicNetLimit();
    long publicNetUsage = chainBaseManager.getDynamicPropertiesStore().getPublicNetUsage();
    long publicNetTime = chainBaseManager.getDynamicPropertiesStore().getPublicNetTime();

    long newPublicNetUsage = increase(publicNetUsage, 0, publicNetTime, now);

    if (bytes > (publicNetLimit - newPublicNetUsage)) {
      logger.debug("Free public net usage is running out."
              + " Bytes: {}, publicNetLimit: {}, newPublicNetUsage: {}.",
          bytes, publicNetLimit, newPublicNetUsage);
      return false;
    }

    latestConsumeFreeTime = now;
    long latestOperationTime = chainBaseManager.getHeadBlockTimeStamp();
    publicNetTime = now;
    newFreeNetUsage = increase(newFreeNetUsage, bytes, latestConsumeFreeTime, now);
    newPublicNetUsage = increase(newPublicNetUsage, bytes, publicNetTime, now);
    accountCapsule.setFreeNetUsage(newFreeNetUsage);
    accountCapsule.setLatestConsumeFreeTime(latestConsumeFreeTime);
    accountCapsule.setLatestOperationTime(latestOperationTime);

    chainBaseManager.getDynamicPropertiesStore().savePublicNetUsage(newPublicNetUsage);
    chainBaseManager.getDynamicPropertiesStore().savePublicNetTime(publicNetTime);
    chainBaseManager.getAccountStore().put(accountCapsule.createDbKey(), accountCapsule);
    return true;

```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L46-78)
```java
  protected long increase(long lastUsage, long usage, long lastTime, long now) {
    return increase(lastUsage, usage, lastTime, now, windowSize);
  }

  protected long increase(long lastUsage, long usage, long lastTime, long now, long windowSize) {
    long averageLastUsage;
    long averageUsage;
    if (hardenCalculation()) {
      BigInteger biPrecision = BigInteger.valueOf(precision);
      BigInteger biWindowSize = BigInteger.valueOf(windowSize);
      averageLastUsage = divideCeilExact(
          BigInteger.valueOf(lastUsage).multiply(biPrecision), biWindowSize);
      averageUsage = divideCeilExact(
          BigInteger.valueOf(usage).multiply(biPrecision), biWindowSize);
    } else {
      averageLastUsage = divideCeil(lastUsage * precision, windowSize);
      averageUsage = divideCeil(usage * precision, windowSize);
    }

    if (lastTime != now) {
      assert now > lastTime;
      if (lastTime + windowSize > now) {
        long delta = now - lastTime;
        double decay = (windowSize - delta) / (double) windowSize;
        averageLastUsage = round(averageLastUsage * decay,
            this.disableJavaLangMath());
      } else {
        averageLastUsage = 0;
      }
    }
    averageLastUsage += averageUsage;
    return getUsage(averageLastUsage, windowSize);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L417-421)
```java
    try {
      this.getPublicNetLimit();
    } catch (IllegalArgumentException e) {
      this.savePublicNetLimit(14_400_000_000L);
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L1234-1258)
```java
  public void savePublicNetUsage(long publicNetUsage) {
    this.put(DynamicResourceProperties.PUBLIC_NET_USAGE,
        new BytesCapsule(ByteArray.fromLong(publicNetUsage)));
  }

  public long getPublicNetUsage() {
    return Optional.ofNullable(getUnchecked(DynamicResourceProperties.PUBLIC_NET_USAGE))
        .map(BytesCapsule::getData)
        .map(ByteArray::toLong)
        .orElseThrow(
            () -> new IllegalArgumentException("not found PUBLIC_NET_USAGE"));
  }

  public void savePublicNetLimit(long publicNetLimit) {
    this.put(DynamicResourceProperties.PUBLIC_NET_LIMIT,
        new BytesCapsule(ByteArray.fromLong(publicNetLimit)));
  }

  public long getPublicNetLimit() {
    return Optional.ofNullable(getUnchecked(DynamicResourceProperties.PUBLIC_NET_LIMIT))
        .map(BytesCapsule::getData)
        .map(ByteArray::toLong)
        .orElseThrow(
            () -> new IllegalArgumentException("not found PUBLIC_NET_LIMIT"));
  }
```
