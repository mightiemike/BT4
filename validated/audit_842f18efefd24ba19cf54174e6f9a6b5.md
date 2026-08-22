### Title
`eth_getTransactionReceipt` always reports transaction status as success regardless of actual execution result - (File: `framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionReceipt.java`)

### Summary
The JSON-RPC `TransactionReceipt` constructor computes the Ethereum-compatible `status` field from `txInfo.getReceipt().getResultValue()` using a condition that is always true for every valid value the field can take, so the receipt always reports `"0x1"` (success) even for reverted/failed transactions.

### Finding Description
`TransactionReceipt` sets the EIP-compatible receipt status with: [1](#0-0) 

`this.status = txInfo.getReceipt().getResultValue() <= 1 ? "0x1" : "0x0";`

`getResultValue()` returns the ordinal of `Protocol.TransactionInfo.code`, which only defines two values: `SUCESS = 0` and `FAILED = 1` (per `protocol/src/main/protos/core/Tron.proto`). Because both possible values (`0` and `1`) satisfy `<= 1`, the ternary always evaluates to `"0x1"`. This mirrors the analog report's bug class: a value is read from the correct object but through a condition/path that can never resolve to the alternate branch, so the derived output field is effectively hard-coded/always-wrong — exactly like `outputTokenAmount` always being `undefined` in the original report, here `status` is always `"0x1"`.

This code path is reached by any anonymous caller of the JSON-RPC `eth_getTransactionReceipt` method, via `formatTransactionResult()` in `TronJsonRpcImpl`, which is invoked unauthenticated over the JSON-RPC HTTP endpoint: [2](#0-1) 

### Impact Explanation
Downstream systems (exchanges, bridges, wallets, DeFi integrations) that rely on the Ethereum JSON-RPC `status` field of a transaction receipt to determine whether a transaction reverted will always see `"0x1"` (success), even when the underlying TVM execution actually failed (`FAILED = 1`). This can lead to accounting/asset corruption in any off-chain system that credits balances, releases funds, or confirms deposits based on the reported receipt status, since it cannot distinguish successful from failed/reverted transactions purely from this field.

### Likelihood Explanation
High likelihood of triggering: any client calling the public, unauthenticated `eth_getTransactionReceipt` JSON-RPC endpoint for a reverted transaction will observe the incorrect status. No privileged access or special conditions are required — a normal failed/reverted `TriggerSmartContract` execution is enough.

### Recommendation
Fix the status derivation to correctly discriminate `SUCESS` (0) from `FAILED` (1), e.g.:
```java
this.status = txInfo.getReceipt().getResultValue() == 0 ? "0x1" : "0x0";
```
or compare against the `Protocol.TransactionInfo.code.SUCESS` enum value explicitly, and add a regression test asserting `"0x0"` status for a `FAILED` `ResourceReceipt`.

### Proof of Concept
1. Execute a smart-contract transaction that reverts (e.g., a `TriggerSmartContract` call that fails execution), producing a `TransactionInfo` whose `receipt.result` is `FAILED` (ordinal `1`).
2. Call `eth_getTransactionReceipt` with that transaction hash against the node's JSON-RPC HTTP endpoint.
3. Observe the returned `status` field is `"0x1"` instead of the expected `"0x0"`, because `getResultValue() <= 1` evaluates true for both `0` (SUCESS) and `1` (FAILED). [3](#0-2)

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionReceipt.java (L73-88)
```java
  public TransactionReceipt(
      BlockCapsule blockCapsule,
      TransactionInfo txInfo,
      TransactionContext context,
      long energyFee) {
    // Set basic fields
    this.blockHash = ByteArray.toJsonHex(blockCapsule.getBlockId().getBytes());
    this.blockNumber = ByteArray.toJsonHex(blockCapsule.getNum());
    this.transactionHash = ByteArray.toJsonHex(txInfo.getId().toByteArray());
    this.transactionIndex = ByteArray.toJsonHex(context.index);
    // Compute cumulative gas until this transaction
    this.cumulativeGasUsed =
        ByteArray.toJsonHex(context.cumulativeGas + txInfo.getReceipt().getEnergyUsageTotal());
    this.gasUsed = ByteArray.toJsonHex(txInfo.getReceipt().getEnergyUsageTotal());
    this.status = txInfo.getReceipt().getResultValue() <= 1 ? "0x1" : "0x0";
    this.effectiveGasPrice = ByteArray.toJsonHex(energyFee);
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L792-815)
```java
  private TransactionResult formatTransactionResult(TransactionInfo transactioninfo, Block block) {
    String txId = ByteArray.toHexString(transactioninfo.getId().toByteArray());

    Transaction transaction = null;
    int transactionIndex = -1;

    List<Transaction> txList = block.getTransactionsList();
    for (int index = 0; index < txList.size(); index++) {
      transaction = txList.get(index);
      if (getTxID(transaction).equals(txId)) {
        transactionIndex = index;
        break;
      }
    }

    if (transactionIndex == -1) {
      return null;
    }

    long energyUsageTotal = transactioninfo.getReceipt().getEnergyUsageTotal();
    BlockCapsule blockCapsule = new BlockCapsule(block);
    return new TransactionResult(blockCapsule, transactionIndex, transaction,
        energyUsageTotal, wallet.getEnergyFee(blockCapsule.getTimeStamp()), wallet);
  }
```
