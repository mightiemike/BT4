### Title
Unlimited Sybil-account exhaustion of the shared `publicNetLimit` free-bandwidth pool - ([File: chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java])

### Summary
`BandwidthProcessor.useFreeNet` draws from a single, chain-wide `publicNetUsage` counter that is shared by every account whose personal `freeNetUsage` has not yet been exhausted. Because the counter tracks only total bytes consumed globally per time window and has no per-account, per-IP, or per-source sub-quota, an attacker who creates many low-balance accounts can consume the entire `publicNetLimit` allotment for the window, denying free bandwidth to other legitimate free-tier senders until the window rolls over.

### Finding Description
In `useFreeNet` [1](#0-0) , a transaction is granted free bandwidth if it fits within both the caller's personal `freeNetLimit` and the network-wide `publicNetLimit`:
- Personal check: `bytes > (freeNetLimit - newFreeNetUsage)` returns false if the account's own quota is exceeded.
- Global check: `bytes > (publicNetLimit - newPublicNetUsage)` returns false if the shared pool is exhausted.

On success, the code does `chainBaseManager.getDynamicPropertiesStore().savePublicNetUsage(newPublicNetUsage)` [2](#0-1)  — a single global counter with no field tracking which address, IP, or session consumed how much of the pool. The getters/setters in `DynamicPropertiesStore` confirm this is one scalar value shared network-wide [3](#0-2) .

Since each freshly-created account starts with `freeNetUsage = 0` (bounded only by its own `freeNetLimit`, e.g. a few thousand bytes per day), an attacker can distribute many small transactions across K different accounts, each individually well within its own `freeNetLimit`, but collectively draining `publicNetLimit` before it resets. There is no consensus-layer accounting that attributes public-pool consumption per source, so nothing in `useFreeNet` prevents one actor controlling many accounts from taking a disproportionate share.

At the API layer, `GlobalRateLimiter` and `RateLimiterInterceptor` only throttle request QPS per IP for RPC/HTTP endpoints [4](#0-3) [5](#0-4) ; this is unrelated to and does not compensate for the on-chain `publicNetUsage` accounting, and can be trivially bypassed by rotating IPs or broadcasting through multiple full nodes/peers, or simply issuing transactions slowly enough to stay under QPS limits while still consuming the shared pool.

### Impact Explanation
Legitimate free-tier accounts relying on the shared public bandwidth pool can be starved for the remainder of the accounting window (`publicNetTime`), forcing them to either pay a transaction fee via `useTransactionFee` or have their transaction rejected with `AccountResourceInsufficientException` [6](#0-5) . This is a DoS against the free-tier accounting mechanism (matching "DoS via the TRON protocol implementation"), not asset theft or state corruption — no funds are stolen, no frozen balances are unfrozen, and paying accounts (frozen-bandwidth or fee-paying) are unaffected.

### Likelihood Explanation
The attack requires only unprivileged capability (account creation + broadcasting signed transactions), matching the allowed threat model. However, each new account costs real TRX (`createAccountFee`/bandwidth cost for `AccountCreateContract`, referenced in `Wallet.java`/`DynamicPropertiesStore.java`), so the attack has a nonzero, scaling economic cost proportional to the number of Sybil accounts needed to exhaust `publicNetLimit`, which is typically a large aggregate value refreshed periodically. This makes the attack feasible but not free, and the impact is self-healing once the time window elapses. This behavior is largely an intrinsic characteristic of the "shared, first-come-first-served" public bandwidth pool design in TRON, rather than a distinct coding defect — there is no bug in the arithmetic or accounting itself, only an absence of a fairness/quota mechanism.

### Recommendation
Introduce a per-account (or per-account-age/per-stake) sub-quota within the shared `publicNetLimit`, e.g., cap how much of the public pool a single address can consume per window, or require a minimum account age/frozen balance before granting access to the public pool, so that Sybil-created zero-balance accounts cannot disproportionately drain the shared resource.

### Proof of Concept
```java
// Conceptual JUnit sketch based on BandwidthProcessorTest patterns
@Test
public void testSybilExhaustsPublicPool() throws Exception {
  dynamicPropertiesStore.savePublicNetLimit(1000L); // small window for test
  dynamicPropertiesStore.savePublicNetUsage(0L);
  dynamicPropertiesStore.savePublicNetTime(0L);

  for (int i = 0; i < 20; i++) {
    AccountCapsule attackerAccount = newZeroBalanceAccount(); // freeNetUsage=0
    TransactionCapsule trx = smallTransferTrx(attackerAccount);
    boolean granted = bandwidthProcessor.useFreeNet(attackerAccount, /*bytes*/ 60, now);
    // early iterations succeed, consuming shared pool
  }

  // Legitimate victim account, freeNetUsage also 0, should also succeed under fair-share
  AccountCapsule victim = newZeroBalanceAccount();
  boolean victimGranted = bandwidthProcessor.useFreeNet(victim, 60, now);

  Assert.assertFalse(victimGranted); // publicNetLimit already exhausted by attacker's Sybil accounts
}
```
Expected result: the victim's free transaction is rejected (`useFreeNet` returns false) purely because a single attacker controlling many cheaply-created accounts consumed the entire shared `publicNetLimit` window, confirming the lack of per-source fairness in `chainBaseManager.getDynamicPropertiesStore().savePublicNetUsage`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L164-176)
```java
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
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L506-547)
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

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/GlobalRateLimiter.java (L23-32)
```java
  public static boolean tryAcquire(RuntimeData runtimeData) {
    String ip = runtimeData.getRemoteAddr();
    if (!Strings.isNullOrEmpty(ip)) {
      RateLimiter r = loadIpLimiter(ip);
      if (r == null || !r.tryAcquire()) {
        return false;
      }
    }
    return rateLimiter.tryAcquire();
  }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java (L104-114)
```java
    IRateLimiter rateLimiter = container
        .get(KEY_PREFIX_RPC, call.getMethodDescriptor().getFullMethodName());

    Listener<ReqT> listener = new ServerCall.Listener<ReqT>() {};

    RuntimeData runtimeData = new RuntimeData(call);
    // Check per-endpoint first to avoid consuming global IP/QPS quota for requests
    // that would be rejected by the per-endpoint limiter anyway. acquirePermit()
    // chooses blocking or non-blocking semantics based on rate.limiter.apiNonBlocking.
    boolean perEndpointAcquired = rateLimiter == null || rateLimiter.acquirePermit(runtimeData);
    boolean acquireResource = perEndpointAcquired && GlobalRateLimiter.acquirePermit(runtimeData);
```
