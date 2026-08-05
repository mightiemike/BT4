## Analysis

The reported bug class — a chain-level "existential deposit" set too low (or effectively absent), enabling attackers to bloat runtime storage with near-zero-cost dust accounts — has a direct analog in java-tron's account-creation fee model.

### Title
Zero-cost account creation via `TransferContract`/`TransferAssetContract`/`AccountCreateContract` enables unbounded `AccountStore` bloat - (File: `actuator/src/main/java/org/tron/core/actuator/TransferActuator.java`, `actuator/src/main/java/org/tron/core/actuator/CreateAccountActuator.java`, `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java`)

### Summary
Unlike Polkadot/Substrate, java-tron has no existential-deposit/account-reaping mechanism at all: an `AccountCapsule` once written to `AccountStore` is never automatically removed, regardless of its balance dropping to zero [1](#0-0) . The only economic deterrent against spamming new accounts is the `CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT` dynamic parameter, which defaults to `0` and is only adjustable later via committee proposal.

### Finding Description
When a `TransferContract`, `TransferAssetContract`, or `AccountCreateContract` targets an address that does not yet exist in `AccountStore`, java-tron implicitly creates a brand-new `AccountCapsule` and charges the sender `dynamicStore.getCreateNewAccountFeeInSystemContract()`: [2](#0-1) [3](#0-2) 

This fee is stored in `DynamicPropertiesStore` under `CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT` and initialized to `0L` at genesis with the comment "changed by committee later": [4](#0-3) 

It is a distinct parameter from `CREATE_ACCOUNT_FEE` (`0.1 TRX` default, used only for account creation triggered internally from contract execution via `BandwidthProcessor.consumeFeeForCreateNewAccount`) [5](#0-4) . No `.conf` file in the repo overrides `CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT`, so unless a governance proposal (`ProposalType.CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT`) has been passed and executed via `ProposalService.process` to raise it above zero, ordinary user-initiated `TransferContract`/`TransferAssetContract`/`AccountCreateContract` transactions that create a brand-new account cost nothing beyond the ordinary bandwidth/byte fee (`TRANSACTION_FEE`, default `10 sun/byte`) [6](#0-5) .

Because java-tron accounts are never reaped when their balance falls to (or starts at) zero — there is simply no equivalent of Substrate's `ExistentialDeposit`/dust-removal logic — every such implicitly created account is a permanent, un-reclaimable entry in `AccountStore`. An attacker who sends a large number of tiny transfers (or `AccountCreateContract` transactions) to distinct never-before-seen addresses can therefore inflate `AccountStore` size indefinitely for a cost bounded only by the per-byte bandwidth fee, not by any per-account minimum deposit.

### Impact Explanation
This matches the report's core impact category: underpriced public work leading to unbounded state growth. `AccountStore` bloat permanently increases node storage/memory requirements and slows down state-root computation, snapshot merges, and sync for all full nodes, since there is no mechanism to reclaim space from these dust accounts. Because the specific governance-controlled fee (`CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT`) starts at `0`, absent an explicit governance proposal, the deterrent cost against this class of attack can be effectively zero.

### Likelihood Explanation
This is reachable by any unprivileged account holder — no special permission is required to send `TransferContract`/`TransferAssetContract`/`AccountCreateContract` transactions to arbitrary new addresses. The only mitigating cost is the base bandwidth fee for the transaction bytes, which is a fixed, low per-byte price unrelated to the long-term storage burden created. Whether this is currently exploitable in a live network depends on whether a committee proposal has already raised `CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT` above its zero default — the code path and the zero-default value are confirmed in-repo, but the current live value on any specific deployed network is a governance/config state not visible from source alone.

### Recommendation
- Ensure `CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT` is set (via governance proposal) to a value that reflects the actual long-term storage cost of a new `AccountCapsule`, not just short-term bandwidth cost, and treat `0` as an unsafe default.
- Consider adding validation/lower-bound constraints in `ProposalUtil.validator` for this specific `ProposalType` to prevent it from being set to (or left at) `0` on production networks.
- Evaluate introducing an account-reaping/minimum-balance mechanism analogous to Substrate's existential deposit, so dust accounts with zero balance can eventually be pruned from `AccountStore`.

### Proof of Concept
1. On a network where `CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT` has not been raised via governance (default `0`), craft a `TransferContract` sending a minimal amount (e.g. `1 sun`) to a freshly generated address that has never appeared on-chain.
2. `TransferActuator.execute` detects `toAccount == null`, creates a new `AccountCapsule`, and adds `dynamicStore.getCreateNewAccountFeeInSystemContract()` (`= 0`) to the fee, so the sender pays only the standard bandwidth fee for the transaction bytes [7](#0-6) .
3. Repeat with a large number of freshly generated addresses; each transaction permanently adds one entry to `AccountStore` with no possibility of automatic removal, since java-tron has no reaping mechanism for zero/low-balance accounts.
4. Total attacker cost scales only with the number of transactions × per-byte bandwidth fee, not with any storage-proportional deposit, mirroring the Pink runtime bloat-attack economics described in the source report.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/AccountStore.java (L91-105)
```java
  @Override
  public void delete(byte[] key) {
    if (CommonParameter.getInstance().isHistoryBalanceLookup()) {
      AccountCapsule old = super.getUnchecked(key);
      if (old != null) {
        recordBalance(old, -old.getBalance());
      }

      BlockCapsule.BlockId blockId = balanceTraceStore.getCurrentBlockId();
      if (blockId != null) {
        accountTraceStore.recordBalanceWithBlock(key, blockId.getNum(), 0);
      }
    }
    super.delete(key);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/TransferActuator.java (L39-66)
```java
    long fee = calcFee();
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    try {
      TransferContract transferContract = any.unpack(TransferContract.class);
      long amount = transferContract.getAmount();
      byte[] toAddress = transferContract.getToAddress().toByteArray();
      byte[] ownerAddress = transferContract.getOwnerAddress().toByteArray();

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
      if (dynamicStore.supportBlackHoleOptimization()) {
        dynamicStore.burnTrx(fee);
      } else {
        adjustBalance(accountStore, accountStore.getBlackhole(), fee);
      }
      adjustBalance(accountStore, toAddress, amount);
```

**File:** actuator/src/main/java/org/tron/core/actuator/CreateAccountActuator.java (L131-134)
```java
  @Override
  public long calcFee() {
    return chainBaseManager.getDynamicPropertiesStore().getCreateNewAccountFeeInSystemContract();
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L84-86)
```java
  private static final byte[] TRANSACTION_FEE = "TRANSACTION_FEE".getBytes(); // 1 byte
  private static final long DEFAULT_TRANSACTION_FEE = 10L;
  public static final String DEFAULT_BANDWIDTH_PRICE_HISTORY = "0:" + DEFAULT_TRANSACTION_FEE;
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L514-518)
```java
    try {
      this.getCreateNewAccountFeeInSystemContract();
    } catch (IllegalArgumentException e) {
      this.saveCreateNewAccountFeeInSystemContract(0L); //changed by committee later
    }
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L247-257)
```java
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
