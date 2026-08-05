### Title
BlockResult with fullTx=true triggers per-transaction TransactionInfo re-lookups instead of reusing the already-fetched block-level list, causing O(n) redundant store reads - (File: framework/src/main/java/org/tron/core/services/jsonrpc/types/BlockResult.java)

### Summary
`BlockResult`'s constructor already bulk-fetches all `TransactionInfo` for the block via `wallet.getTransactionInfoByBlockNum(...)` [1](#0-0) , but when building each `TransactionResult` for `UnfreezeBalanceContract`/`WithdrawBalanceContract`/`UnfreezeBalanceV2Contract`/etc. it calls `JsonRpcApiUtil.getTransactionAmount(contract, hash, wallet)` [2](#0-1)  which internally performs an independent `wallet.getTransactionInfoById(hash)` lookup per matching contract [3](#0-2)  rather than reusing the already-in-hand `transactionInfoList`. This produces redundant per-transaction store reads scaling with the number of such contracts in the requested block.

### Finding Description
An attacker who calls `eth_getBlockByNumber`/`eth_getBlockByHash` with `fullTx=true` on a block reaches `TronJsonRpcImpl.getBlockResult` → `new BlockResult(block, fullTx, wallet)` [4](#0-3) . Inside `BlockResult`, the code fetches the block's full `TransactionInfo` list once via `wallet.getTransactionInfoByBlockNum` [1](#0-0) , but then for each transaction, when `fullTx` is true, constructs a `TransactionResult(blockCapsule, i, transaction, energyUsageTotal, energyFee, wallet)` [5](#0-4)  without passing the already-fetched `TransactionInfo` for that index. Inside `TransactionResult`, `getTransactionAmount(contract, hash, wallet)` is invoked (the 3-arg overload) [6](#0-5) , and for `UnfreezeBalanceContract`, `WithdrawBalanceContract`, `WithdrawExpireUnfreezeContract`, `UnfreezeBalanceV2Contract`, `CancelAllUnfreezeV2Contract` this overload performs its own independent `wallet.getTransactionInfoById(...)` call per contract [7](#0-6) , duplicating a lookup for data that was already retrieved in bulk moments earlier. There is a second overload, `getTransactionAmount(contract, hash, transactionInfo, wallet)` [8](#0-7) , that accepts a pre-fetched `TransactionInfo` and avoids the extra lookup, but `TransactionResult`'s constructor does not use it — it always calls the no-`TransactionInfo` overload, forcing redundant individual reads. There is no batching, caching, or memoization across the block; each qualifying contract triggers its own store call, so a block packed with N such contracts causes N extra individual reads on top of the one bulk fetch already performed.

### Impact Explanation
This is a performance/availability concern (denial-of-service / resource-exhaustion via underpriced work) rather than a fund-theft or consensus-divergence bug. An attacker cannot manipulate accounting, replay transactions, or forge value — `getTransactionAmount`'s output only affects a read-only RPC response field. The impact is limited to increased CPU/IO cost per `eth_getBlockByNumber`/`eth_getBlockByHash(fullTx=true)` call on blocks with many freeze/unfreeze/withdraw-type contracts, which could be used to amplify load on a full node's JSON-RPC service relative to the cost of a normal request of equal size.

### Likelihood Explanation
The condition requires that a block already contains many `UnfreezeBalanceContract`/`WithdrawBalanceContract`/`UnfreezeBalanceV2Contract`/etc. transactions. An attacker can influence how many of these contract types appear in blocks they submit (subject to normal chain resource/energy fee limits for building transactions), but cannot force an entire block to be one type without cooperation of block producers/other transaction volume, and each such transaction itself costs the sender bandwidth/energy to include on-chain — this is a real, but non-trivial-to-mount, resource-amplification vector requiring the attacker to have already paid for many on-chain transactions of the affected type to exist in a single queried block.

### Recommendation
Modify `TransactionResult`'s constructor to accept the already-fetched `TransactionInfo` for the current transaction index (from `BlockResult`'s `transactionInfoList`) and call the `getTransactionAmount(contract, hash, transactionInfo, wallet)` overload instead of the overload that triggers `wallet.getTransactionInfoById(...)`. This eliminates the duplicate per-transaction store lookup entirely since the data is already available in memory from the single bulk `getTransactionInfoByBlockNum` call.

### Proof of Concept
Java integration test plan (in `framework/src/test`):
1. Build (or use `TransactionCapsule`/`BlockCapsule` test helpers) a block with N `UnfreezeBalanceContract` (or `UnfreezeBalanceV2Contract`/`WithdrawBalanceContract`) transactions, and populate the corresponding `TransactionInfoStore`/`TransactionRetStore` entries so `wallet.getTransactionInfoById` and `wallet.getTransactionInfoByBlockNum` return matching data.
2. Instrument/spy `Wallet.getTransactionInfoById` (e.g., via Mockito spy) to count invocations.
3. Call `new BlockResult(block, true, wallet)` and assert that `getTransactionInfoById` is invoked exactly N times (once per qualifying contract) despite `getTransactionInfoByBlockNum` already having fetched the same N `TransactionInfo` records in one call.
4. Vary N from 10 to 10,000 and record wall-clock time; compare against an equally sized block of `TransferContract` transactions (which do not trigger `getTransactionInfoById`) to show call count / time scaling with N for the affected contract types but not for `TransferContract`.
5. After applying the fix (passing `transactionInfo` into `TransactionResult`), re-run the same test and assert `getTransactionInfoById` call count is 0 for the `fullTx` code path, confirming the redundant lookups are removed.

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/types/BlockResult.java (L124-125)
```java
    List<TransactionInfo> transactionInfoList =
        wallet.getTransactionInfoByBlockNum(blockCapsule.getNum()).getTransactionInfoList();
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/types/BlockResult.java (L136-137)
```java
        txes.add(new TransactionResult(blockCapsule, i, transaction,
            energyUsageTotal, energyFee, wallet));
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionResult.java (L117-118)
```java
      to = ByteArray.toJsonHexAddress(toByte);
      value = ByteArray.toJsonHex(getTransactionAmount(contract, hash, wallet));
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java (L221-234)
```java
  public static long getTransactionAmount(Transaction.Contract contract, String hash,
      Wallet wallet) {
    long amount = 0;
    try {
      switch (contract.getType()) {
        case UnfreezeBalanceContract:
        case WithdrawBalanceContract:
        case WithdrawExpireUnfreezeContract:
        case UnfreezeBalanceV2Contract:
        case CancelAllUnfreezeV2Contract:
          TransactionInfo transactionInfo = wallet
              .getTransactionInfoById(ByteString.copyFrom(ByteArray.fromHexString(hash)));
          amount = getAmountFromTransactionInfo(hash, contract.getType(), transactionInfo);
          break;
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java (L247-309)
```java
  public static long getTransactionAmount(Transaction.Contract contract, String hash,
                                          TransactionInfo transactionInfo, Wallet wallet) {
    long amount = 0;
    try {
      Any contractParameter = contract.getParameter();
      switch (contract.getType()) {
        case TransferContract:
          amount = contractParameter.unpack(TransferContract.class).getAmount();
          break;
        case TransferAssetContract:
          amount = contractParameter.unpack(TransferAssetContract.class).getAmount();
          break;
        case VoteWitnessContract:
          List<Vote> votesList = contractParameter.unpack(VoteWitnessContract.class).getVotesList();
          long voteNumber = 0L;
          for (Vote vote : votesList) {
            voteNumber += vote.getVoteCount();
          }
          amount = voteNumber;
          break;
        case WitnessCreateContract:
          amount = 9999_000_000L;
          break;
        case AssetIssueContract:
        case ExchangeCreateContract:
          amount = 1024_000_000L;
          break;
        case ParticipateAssetIssueContract:
          break;
        case FreezeBalanceContract:
          amount = contractParameter.unpack(FreezeBalanceContract.class).getFrozenBalance();
          break;
        case TriggerSmartContract:
          amount = contractParameter.unpack(TriggerSmartContract.class).getCallValue();
          break;
        case ExchangeInjectContract:
          amount = contractParameter.unpack(ExchangeInjectContract.class).getQuant();
          break;
        case ExchangeWithdrawContract:
          amount = contractParameter.unpack(ExchangeWithdrawContract.class).getQuant();
          break;
        case ExchangeTransactionContract:
          amount = contractParameter.unpack(ExchangeTransactionContract.class).getQuant();
          break;
        case AccountPermissionUpdateContract:
          amount = 100_000_000L;
          break;
        case ShieldedTransferContract:
          ShieldedTransferContract shieldedTransferContract = contract.getParameter()
              .unpack(ShieldedTransferContract.class);
          if (shieldedTransferContract.getFromAmount() > 0L) {
            amount = shieldedTransferContract.getFromAmount();
          } else if (shieldedTransferContract.getToAmount() > 0L) {
            amount = shieldedTransferContract.getToAmount();
          }
          break;
        case UnfreezeBalanceContract:
        case WithdrawBalanceContract:
        case WithdrawExpireUnfreezeContract:
        case UnfreezeBalanceV2Contract:
        case CancelAllUnfreezeV2Contract:
          amount = getAmountFromTransactionInfo(hash, contract.getType(), transactionInfo);
          break;
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L409-415)
```java
  private BlockResult getBlockResult(Block block, boolean fullTx) {
    if (block == null) {
      return null;
    }

    return new BlockResult(block, fullTx, wallet);
  }
```
