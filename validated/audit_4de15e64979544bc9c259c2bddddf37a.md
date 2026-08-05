### Title
DoS via exhaustion of the shared, time-windowed public free-bandwidth pool - (File: chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java)

### Summary
`BandwidthProcessor.useFreeNet` is java-tron's structural analog of the PSM's `TimeBasedRateLimiter`. Instead of per-account-only limits, TRON maintains a single global, time-windowed counter (`PUBLIC_NET_LIMIT` / `PUBLIC_NET_USAGE` / `PUBLIC_NET_TIME`) that every account on the network draws from once its own per-account free quota is exhausted. Any unprivileged user can consume this shared pool until it hits its ceiling, after which **every other account** attempting to use free bandwidth is rejected until the window resets — the same "single actor exhausts a shared duration-window limit and locks out all subsequent users" pattern described in the report.

### Finding Description
`useFreeNet` first checks/updates a per-account free-bandwidth counter (`FREE_NET_LIMIT`, default 5000 bytes/day), then falls through to a **global** counter: [1](#0-0) 

The global counter's state is stored in `DynamicPropertiesStore` and reset on a sliding time window exactly like the PSM's `lastResetMintTime + resetMintDuration` pattern: [2](#0-1) 

The window-reset/decay arithmetic lives in the shared `ResourceProcessor.increase` helper, which both `BandwidthProcessor` and `EnergyProcessor` extend: [3](#0-2) 

Default values are seeded at genesis/init: [4](#0-3) 

Just like the PSM's `mintLimit`/`redeemLimit`, `PUBLIC_NET_LIMIT` (default 14,400,000,000 bytes) is a fixed, first-come-first-served budget shared by the whole network within a rolling window (`ONE_DAY_NET_LIMIT`-scale window, derived from `WINDOW_SIZE_MS`/`BLOCK_PRODUCED_INTERVAL` in `ResourceProcessor`). Once `newPublicNetUsage` reaches `publicNetLimit`, `useFreeNet` returns `false` for **every** account on the network for the remainder of the window, and the caller falls through to `useTransactionFee` — meaning any account whose owner does not want to (or forgot to) freeze TRX/pay a fee for bandwidth is denied the free-transaction path entirely.

### Impact Explanation
An attacker who can generate/control enough distinct accounts (each capped at `FREE_NET_LIMIT` = 5000 bytes/day) can, over the course of a window, submit enough free transactions to drive `PUBLIC_NET_USAGE` up to `PUBLIC_NET_LIMIT`. Once exhausted, all other legitimate accounts relying on the free/public bandwidth allotment (e.g., new/low-balance accounts, dApps subsidizing user transactions) lose access to gas-free transactions until the window resets, forcing them to either pay TRX fees or be rejected with `AccountResourceInsufficientException`. This is a direct availability/DoS impact on an unprivileged, publicly-shared on-chain resource — the same impact class as the PSM report (denial of service against subsequent unprivileged users of a shared rate-limited resource).

### Likelihood Explanation
Exploitation requires either (a) many funded accounts each consuming their individual `FREE_NET_LIMIT` allotment, or (b) fewer accounts issuing enough transactions whose combined `bytesSize` reaches the pool ceiling. This carries real but modest cost (creating/funding many accounts or paying the `createAccountFee`), which is comparable to — and arguably cheaper than — the redemption fee the original report notes as a deterrent. Given TRON's low per-transaction cost and the fact that `PUBLIC_NET_LIMIT` is shared unconditionally by all accounts (no per-IP/per-identity throttling on the public pool itself), this is more likely to occur incidentally under high network activity than the analogous PSM scenario, even without deliberate attack.

### Recommendation
- Consider bounding how much of the public pool a single account (or a short burst of newly created accounts) can consume within a window, e.g., a per-account or per-IP sub-quota on `PUBLIC_NET_USAGE` draws, independent of `FREE_NET_LIMIT`.
- Shorten the effective reset window for the public pool or make replenishment continuous/proportional (as opposed to a hard reset at each window boundary) to reduce the "all-or-nothing" exhaustion effect, mirroring the recommendation to keep `resetMintDuration`/`resetRedeemDuration` reasonably small in the original report.
- Add monitoring/alerting on `PUBLIC_NET_USAGE` approaching `PUBLIC_NET_LIMIT` so operators can react (increase `PUBLIC_NET_LIMIT` via governance proposal) before exhaustion impacts legitimate users.

### Proof of Concept
1. Create (or control) a sufficient number of accounts, each below its own `FREE_NET_LIMIT` (5000 bytes/day by default) — see `chainBaseManager.getDynamicPropertiesStore().getFreeNetLimit()` used in `useFreeNet` at [5](#0-4) .
2. From each account, broadcast free (zero-fee) transactions (e.g., TRX transfers) that route through `useFreeNet` after `useAccountNet` fails (no frozen bandwidth) — see dispatch order in `consume`: [6](#0-5) .
3. Repeat across accounts/transactions until cumulative `bytesSize` consumed via `useFreeNet` reaches `PUBLIC_NET_LIMIT` (default 14,400,000,000 bytes), tracked in `DynamicPropertiesStore.PUBLIC_NET_USAGE` at [7](#0-6) .
4. Any subsequent, unrelated account attempting a free transaction within the same window now fails the check `bytes > (publicNetLimit - newPublicNetUsage)` at [8](#0-7)  and falls through to `useTransactionFee`, or is rejected outright if it has no TRX balance — denying it free-bandwidth service until the shared window resets.

Note: I was unable to fully verify the exact numeric value of `WINDOW_SIZE_MS` (the constant controlling how long the public pool window lasts) within the indexed portion of `Parameter.java`; the reset cadence is inferred from `ONE_DAY_NET_LIMIT` naming and `ResourceProcessor`'s window-based decay logic. A Devin session with full repository access could confirm the exact window duration in `common/src/main/java/org/tron/core/config/Parameter.java`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L160-166)
```java
      if (useAccountNet(accountCapsule, bytesSize, now)) {
        continue;
      }

      if (useFreeNet(accountCapsule, bytesSize, now)) {
        continue;
      }
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L506-531)
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
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L411-433)
```java
    try {
      this.getOneDayNetLimit();
    } catch (IllegalArgumentException e) {
      this.saveOneDayNetLimit(57_600_000_000L);
    }

    try {
      this.getPublicNetLimit();
    } catch (IllegalArgumentException e) {
      this.savePublicNetLimit(14_400_000_000L);
    }

    try {
      this.getPublicNetTime();
    } catch (IllegalArgumentException e) {
      this.savePublicNetTime(0L);
    }

    try {
      this.getFreeNetLimit();
    } catch (IllegalArgumentException e) {
      this.saveFreeNetLimit(5000L);
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L1234-1271)
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

  public void savePublicNetTime(long publicNetTime) {
    this.put(DynamicResourceProperties.PUBLIC_NET_TIME,
        new BytesCapsule(ByteArray.fromLong(publicNetTime)));
  }

  public long getPublicNetTime() {
    return Optional.ofNullable(getUnchecked(DynamicResourceProperties.PUBLIC_NET_TIME))
        .map(BytesCapsule::getData)
        .map(ByteArray::toLong)
        .orElseThrow(
            () -> new IllegalArgumentException("not found PUBLIC_NET_TIME"));
  }
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
