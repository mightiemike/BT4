### Title
Zero-cost dust-account bloat via implicit account creation in `TransferActuator`/`TransferAssetContract` - (File: `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java`, `actuator/src/main/java/org/tron/core/actuator/TransferActuator.java`, `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java`)

### Summary
Java-tron has no account-reaping / existential-deposit mechanism: once an `AccountCapsule` is written to the `AccountStore` it is never removed, regardless of balance. The fee that is supposed to make implicit account creation costly — `CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT` — defaults to `0` and is only "changed by committee later", while the bandwidth cost required to create such an account is negligible (a few thousand `sun`, i.e. a fraction of a millionth of a TRX). This mirrors the reported Pink-runtime bug class: an unprivileged actor can broadcast ordinary transactions to permanently bloat chain storage at effectively zero cost, because the guard value equivalent to the "Existential Deposit" is set to zero by default and there is no account-reaping equivalent.

### Finding Description
Implicit account creation happens in `TransferActuator.execute` and in `BandwidthProcessor.contractCreateNewAccount`/`consumeForCreateNewAccount`: when a `TransferContract` (or `TransferAssetContract`) targets a `toAddress` that does not yet have an `AccountCapsule`, a new one is created and persisted in `AccountStore` unconditionally. [1](#0-0) 

The only monetary charge tied to this implicit creation is `dynamicStore.getCreateNewAccountFeeInSystemContract()`, which is added on top of the transfer amount. [2](#0-1) 

That fee's default persisted value is `0` — the code comment even says it is meant to be "changed by committee later," i.e. the protocol ships with no floor: [3](#0-2) 

The `ProposalType` enum documents the current on-chain value as `0 TRX` and only bounds it at `[0, 100000000000]` — i.e. it is legal (and currently is) `0`, unlike the explicit `CreateAccountActuator` fee (`CREATE_ACCOUNT_FEE`, default `0.1 TRX`) which is a *different*, unused-for-implicit-creation parameter: [4](#0-3) 

Besides this fee, the only other cost is bandwidth, and `BandwidthProcessor.consumeForCreateNewAccount` first tries to pay for the new-account bandwidth out of the sender's *free* bandwidth allowance, and only falls back to `getCreateAccountFee()`-priced bandwidth if that free quota is exhausted: [5](#0-4) 

Every account additionally gets a `FREE_NET_LIMIT` of 5000 bytes/day and there is a shared `PUBLIC_NET_LIMIT` of 14.4B bytes, both refreshed periodically, which can be used to pay for the create-account bandwidth entirely for free: [6](#0-5) 

Because `CreateAccountActuator`/`TransferActuator` never remove the created `AccountCapsule` even if its balance later drops to (or starts at) zero, and there is no account-deletion path anywhere in `AccountStore`/actuators (confirmed by absence of any delete-on-zero-balance logic), every such account is a **permanent** state entry.

### Impact Explanation
An attacker who controls a single funded account can broadcast a very large number of `TransferContract` transactions to freshly generated addresses (any valid address string, no private key needed to receive), each transferring a minimal amount (even `1 sun`). Each such transaction:
- Costs `0` TRX in `CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT` (current default),
- Costs only bandwidth, which can be paid from the daily free bandwidth allowance or via the cheap `TRANSACTION_FEE` (10 sun/byte) fallback,
- Permanently creates a new `AccountCapsule` row in `AccountStore` that is never reclaimed.

This lets an attacker bloat validator storage (and by extension state-sync/snapshot size, and the cost of anyone iterating `AccountStore`) at negligible cost, exactly the "bloat attack" class described in the report, and it is reachable purely from broadcasting ordinary signed transactions — no privileged role required.

### Likelihood Explanation
This requires no elevated privilege and is directly reachable via the public gRPC/HTTP `BroadcastTransaction` API using standard `TransferContract`/`TransferAssetContract` messages. The only friction is the attacker's own available bandwidth/energy and TRX to fund transaction fees for the bandwidth fallback, both of which scale linearly and cheaply (fraction of a TRX per thousand accounts, given `TRANSACTION_FEE` = 10 sun/byte and typical transfer size ~100-200 bytes) — likelihood is high given current default parameter values, similar in spirit to the original Pink `ExistentialDeposit = 1` report.

### Recommendation
- Ensure `CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT` is set (and enforced with a sane non-zero floor in `ProposalUtil` validation) to a value that meaningfully offsets the marginal storage cost of a permanent account entry, not just `[0, LONG_VALUE]`.
- Consider introducing an account-reaping mechanism (akin to Substrate's Existential Deposit) so that accounts with zero/near-zero balance and no other state are removed from `AccountStore` after some period, bounding worst-case storage growth from dust accounts.
- Align the implicit-creation fee path with the explicit `CreateAccountActuator` fee (`CREATE_ACCOUNT_FEE`) so both creation paths carry comparable, non-negligible cost.

### Proof of Concept
1. Fund one attacker-controlled account with a small TRX balance.
2. Repeatedly submit `TransferContract` transactions with `amount = 1` (or similarly minimal) to freshly generated `toAddress` values that have no existing `AccountCapsule`.
3. Observe in `TransferActuator.execute` that each such transaction creates and persists a new `AccountCapsule` via `accountStore.put(toAddress, toAccount)` while charging `fee = dynamicStore.getCreateNewAccountFeeInSystemContract()` (currently `0`) plus only bandwidth cost, which is paid for free until the sender's `FREE_NET_LIMIT`/`PUBLIC_NET_LIMIT` is exhausted, after which it falls back to the cheap `TRANSACTION_FEE` (10 sun/byte).
4. Repeat at scale (bounded only by attacker TRX spent on the cheap bandwidth fallback) to grow `AccountStore` size arbitrarily, with no mechanism ever removing the created accounts. [7](#0-6)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/TransferActuator.java (L48-60)
```java
      // if account with to_address does not exist, create it first.
      AccountCapsule toAccount = accountStore.get(toAddress);
      if (toAccount == null) {
        boolean withDefaultPermission =
            dynamicStore.getAllowMultiSign() == 1;
        toAccount = new AccountCapsule(ByteString.copyFrom(toAddress), AccountType.Normal,
            dynamicStore.getLatestBlockHeaderTimestamp(), withDefaultPermission, dynamicStore);
        accountStore.put(toAddress, toAccount);

        fee = fee + dynamicStore.getCreateNewAccountFeeInSystemContract();
      }

      adjustBalance(accountStore, ownerAddress, -(addExact(fee, amount)));
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L417-433)
```java
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

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L514-518)
```java
    try {
      this.getCreateNewAccountFeeInSystemContract();
    } catch (IllegalArgumentException e) {
      this.saveCreateNewAccountFeeInSystemContract(0L); //changed by committee later
    }
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L951-958)
```java
    ACCOUNT_UPGRADE_COST(1), // 9999 TRX, [0, 100000000000] TRX
    CREATE_ACCOUNT_FEE(2), // 0.1 TRX, [0, 100000000000] TRX
    TRANSACTION_FEE(3), // 10 Sun/Byte, [0, 100000000000] TRX
    ASSET_ISSUE_FEE(4), // 1024 TRX, [0, 100000000000] TRX
    WITNESS_PAY_PER_BLOCK(5), // 16 TRX, [0, 100000000000] TRX
    WITNESS_STANDBY_ALLOWANCE(6), // 115200 TRX, [0, 100000000000] TRX
    CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT(7), // 0 TRX, [0, 100000000000] TRX
    CREATE_NEW_ACCOUNT_BANDWIDTH_RATE(8), // 1 Bandwith/Byte, [0, 100000000000000000] Bandwith/Byte
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L192-257)
```java
  private void consumeForCreateNewAccount(AccountCapsule accountCapsule, long bytes,
      long now, TransactionTrace trace)
      throws AccountResourceInsufficientException {
    boolean ret = consumeBandwidthForCreateNewAccount(accountCapsule, bytes, now, trace);

    if (!ret) {
      ret = consumeFeeForCreateNewAccount(accountCapsule, trace);
      if (!ret) {
        throw new AccountResourceInsufficientException(String.format(
            "account [%s] has insufficient bandwidth[%d] and balance[%d] to create new account",
            StringUtil.encode58Check(accountCapsule.createDbKey()), bytes,
            chainBaseManager.getDynamicPropertiesStore().getCreateAccountFee()));
      }
    }
  }

  public boolean consumeBandwidthForCreateNewAccount(AccountCapsule accountCapsule, long bytes,
      long now, TransactionTrace trace) {

    long createNewAccountBandwidthRatio = chainBaseManager.getDynamicPropertiesStore()
        .getCreateNewAccountBandwidthRate();

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

    long netCost = bytes * createNewAccountBandwidthRatio;
    if (netCost <= (netLimit - newNetUsage)) {
      long latestOperationTime = chainBaseManager.getHeadBlockTimeStamp();
      if (!dynamicPropertiesStore.supportUnfreezeDelay()) {
        newNetUsage = increase(newNetUsage, netCost, now, now);
      } else {
        // Participate in calculation and flush disk persistence
        newNetUsage = increase(accountCapsule, BANDWIDTH,
            netUsage, netCost, latestConsumeTime, now);
      }
      accountCapsule.setLatestConsumeTime(now);
      accountCapsule.setLatestOperationTime(latestOperationTime);
      accountCapsule.setNetUsage(newNetUsage);

      trace.setNetBillForCreateNewAccount(netCost, 0);
      chainBaseManager.getAccountStore().put(accountCapsule.createDbKey(), accountCapsule);

      return true;
    }
    return false;
  }

  public boolean consumeFeeForCreateNewAccount(AccountCapsule accountCapsule,
      TransactionTrace trace) {
    long fee = chainBaseManager.getDynamicPropertiesStore().getCreateAccountFee();
    if (consumeFeeForNewAccount(accountCapsule, fee)) {
      trace.setNetBillForCreateNewAccount(0, fee);
      chainBaseManager.getDynamicPropertiesStore().addTotalCreateAccountCost(fee);
      return true;
    } else {
      return false;
    }
  }
```
