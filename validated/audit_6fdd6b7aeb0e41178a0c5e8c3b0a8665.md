## Title
Single low-balance TRC10 holder can drain the shared free-bandwidth pool and exhaust the issuer's own bandwidth budget via wallet cycling - (File: `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java`)

### Summary
`BandwidthProcessor.useAssetAccountNet()` grants free bandwidth for `TransferAssetContract` transfers of a TRC10 asset by checking three counters: a per-account `freeAssetNetUsage`, a global `publicFreeAssetNetUsage`, and the token issuer's own `netUsage` (bounded by `issuerNetLimit`, computed from the issuer's frozen TRX). The per-account counter resets to zero for every freshly created account, while the global `publicFreeAssetNetLimit`/`publicFreeAssetNetUsage` and the issuer's `netUsage` are shared, capped resources consumed by *any* holder's transfer. Because owning even 1 unit of the asset is sufficient to trigger a free-bandwidth-consuming transfer, and the gating collateral (the 1 token unit) is never burned/locked, an attacker can shuttle that single unit through an unbounded number of throwaway accounts to repeatedly re-enter the "fresh wallet, zero usage" state — this mirrors the token-gated-drop drain pattern (minimal collateral + resettable per-wallet counter + shared capped resource).

### Finding Description
In `useAssetAccountNet` (`chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java:290-430`):
- The per-account gating check uses `accountCapsule.getFreeAssetNetUsage(tokenID)` / `getFreeAssetNetUsageV2(tokenID)`, which is always `0` for a brand-new account: [1](#0-0) 
- The requirement to be eligible at all is merely holding `tokenQuant` of the asset (`assetBalanceEnoughV2`), i.e., an actor only needs 1 unit of the token — never consumed by the free-bandwidth check itself, so it can be moved to the next fresh wallet and reused, exactly like the un-consumed "gating token" in the NFT report: [2](#0-1) 
- The two capped/shared resources actually consumed on each such transfer are:
  1. `publicFreeAssetNetLimit`/`publicFreeAssetNetUsage` — a single global counter shared by every holder of the asset (analogous to the drop's `maxTokenSupplyForStage`): [3](#0-2) 
  2. The **issuer's own account** `netUsage`, capped by `issuerNetLimit` computed from the issuer's frozen TRX bandwidth budget — this is the same counter used for the issuer's own ordinary transactions: [4](#0-3) [5](#0-4) 

Because the per-account `freeAssetNetUsage`/`latestAssetOperationTime` counters live on the *account*, and accounts are free to create (any address can receive an asset and appear as an "account" the moment it holds balance), an attacker holding just `1` unit of the asset can:
1. Transfer the 1 unit to wallet `w1` (a fresh account, `freeAssetNetUsage = 0` for this token) — pays ordinary bandwidth/fee for creating `w1` but the *asset transfer itself* consumes free bandwidth from `publicFreeAssetNetLimit` and `issuerNetLimit`.
2. Transfer the same 1 unit from `w1` to `w2`, again a fresh account, again consuming free bandwidth from the same two shared pools.
3. Repeat with `w3, w4, …, wn` until `publicFreeAssetNetLimit` or the issuer's `issuerNetLimit` for the recovery window is exhausted.

At no point does this require more than the trivial 1-unit balance and the base TRX to cover the (fixed, tiny) account-creation/entropy cost, mirroring the report's core flaw: a per-wallet gate that can be reset by moving unconsumed collateral to a new wallet, draining a globally shared, hard-capped resource meant to be split among many independent participants.

### Impact Explanation
This is a concrete, unprivileged Denial-of-Service / resource-accounting-abuse vector:
- Legitimate holders of the TRC10 asset lose access to the free-bandwidth allowance the issuer intended to provide the community, because `publicFreeAssetNetUsage` is exhausted by one actor.
- More seriously, the issuer's own account bandwidth (`issuerAccountCapsule.getNetUsage()`/`issuerNetLimit`) — the same counter governing the issuer's *own* ordinary transactions — gets consumed by an attacker who never interacts with the issuer directly, potentially forcing the issuer to pay TRX fees for basic operations or fail transactions due to bandwidth exhaustion. This is a resource-accounting corruption/DoS against a specific victim account (the issuer) triggered purely by broadcasting cheap `TransferAssetContract` transactions.

### Likelihood Explanation
High likelihood of feasibility: the attack only requires normal, unprivileged wallet creation (free on TRON) and possession of 1 unit of any TRC10 asset with a nonzero `freeAssetNetLimit`/`publicFreeAssetNetLimit`, which is common configuration for popular TRC10 tokens. No special permissions, signatures, or race conditions are needed — just repeated `TransferAssetContract` broadcasts.

### Recommendation
- Tie the per-account free-asset-net-usage exemption to account age/activity or require a minimum retained balance duration, so that freshly created/emptied accounts cannot repeatedly reset the free quota.
- Consider rate-limiting or charging normal bandwidth/fee for TRC10 transfers below a minimum value threshold, so trivial 1-unit transfers cannot be used purely to drain the shared free-bandwidth pools.
- Decouple the community's `publicFreeAssetNetLimit`/`issuerNetLimit` consumption from unconsumed, re-transferable collateral, e.g., by consuming from `issuerNetLimit` only proportionally to unique holder count or by capping the number of free transfers per token per block regardless of the number of distinct accounts used.

### Proof of Concept
1. Issuer creates TRC10 asset `N` with nonzero `free_asset_net_limit` and `public_free_asset_net_limit`.
2. Attacker acquires `1` unit of `N` in wallet `w0`.
3. Loop: `TransferAssetContract(N, 1, w_i -> w_{i+1})` for fresh `w_{i+1}` each iteration — each call hits `useAssetAccountNet` in `BandwidthProcessor.java`, consuming `publicFreeAssetNetUsage` and the issuer's `netUsage` while `w_{i+1}`'s own `freeAssetNetUsage` starts at 0.
4. Continue until `publicFreeAssetNetLimit` or `issuerNetLimit` is exhausted for the recovery window, denying free bandwidth to all other holders and consuming the issuer's own bandwidth budget.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L317-329)
```java
    long publicFreeAssetNetLimit = assetIssueCapsule.getPublicFreeAssetNetLimit();
    long publicFreeAssetNetUsage = assetIssueCapsule.getPublicFreeAssetNetUsage();
    long publicLatestFreeNetTime = assetIssueCapsule.getPublicLatestFreeNetTime();

    long newPublicFreeAssetNetUsage = increase(publicFreeAssetNetUsage, 0,
        publicLatestFreeNetTime, now);

    if (bytes > (publicFreeAssetNetLimit - newPublicFreeAssetNetUsage)) {
      logger.debug("The {} public free bandwidth is not enough."
              + " Bytes: {}, publicFreeAssetNetLimit: {}, newPublicFreeAssetNetUsage: {}.",
          tokenID, bytes, publicFreeAssetNetLimit,  newPublicFreeAssetNetUsage);
      return false;
    }
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L331-353)
```java
    long freeAssetNetLimit = assetIssueCapsule.getFreeAssetNetLimit();

    long freeAssetNetUsage;
    long latestAssetOperationTime;
    if (chainBaseManager.getDynamicPropertiesStore().getAllowSameTokenName() == 0) {
      freeAssetNetUsage = accountCapsule
          .getFreeAssetNetUsage(tokenName);
      latestAssetOperationTime = accountCapsule
          .getLatestAssetOperationTime(tokenName);
    } else {
      freeAssetNetUsage = accountCapsule.getFreeAssetNetUsageV2(tokenID);
      latestAssetOperationTime = accountCapsule.getLatestAssetOperationTimeV2(tokenID);
    }

    long newFreeAssetNetUsage = increase(freeAssetNetUsage, 0,
        latestAssetOperationTime, now);

    if (bytes > (freeAssetNetLimit - newFreeAssetNetUsage)) {
      logger.debug("The {} free bandwidth is not enough."
              + " Bytes: {}, freeAssetNetLimit: {}, newFreeAssetNetUsage:{}.",
          tokenID, bytes, freeAssetNetLimit, newFreeAssetNetUsage);
      return false;
    }
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L355-376)
```java
    AccountCapsule issuerAccountCapsule = chainBaseManager.getAccountStore()
        .get(assetIssueCapsule.getOwnerAddress().toByteArray());

    long issuerNetUsage = issuerAccountCapsule.getNetUsage();
    long latestConsumeTime = issuerAccountCapsule.getLatestConsumeTime();
    long issuerNetLimit = calculateGlobalNetLimit(issuerAccountCapsule);
    long newIssuerNetUsage;
    if (!dynamicPropertiesStore.supportUnfreezeDelay()) {
      newIssuerNetUsage = increase(issuerNetUsage, 0, latestConsumeTime, now);
    } else {
      // only participate in the calculation as a temporary variable, without disk flushing
      newIssuerNetUsage = recovery(issuerAccountCapsule, BANDWIDTH, issuerNetUsage,
          latestConsumeTime, now);
    }

    if (bytes > (issuerNetLimit - newIssuerNetUsage)) {
      logger.debug("The {} issuer's bandwidth is not enough."
              + " Bytes: {}, issuerNetLimit: {}, newIssuerNetUsage:{}.",
          tokenID, bytes, issuerNetLimit, newIssuerNetUsage);
      return false;
    }

```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L393-394)
```java
    issuerAccountCapsule.setNetUsage(newIssuerNetUsage);
    issuerAccountCapsule.setLatestConsumeTime(now);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L242-246)
```java
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(tokenID, tokenQuant, dynamicStore)) {
        throw new ContractValidateException("token balance is not enough");
      }
    }
```
