### Title
`eth_getTransactionByHash` always reports `value=0x0` for `ParticipateAssetIssueContract` TRX payments - ([File: JsonRpcApiUtil.java])

### Summary
`ParticipateAssetIssueActuator.execute` moves real TRX from the buyer to the asset issuer (`cost = participateAssetIssueContract.getAmount()`, subtracted from owner's balance and added to the issuer's balance), but `JsonRpcApiUtil.getTransactionAmount` has an empty `case ParticipateAssetIssueContract: break;` branch, leaving `amount = 0`. This value is surfaced verbatim by `TransactionResult.value` in `eth_getTransactionByHash`/`eth_getTransactionReceipt`-style JSON-RPC responses.

### Finding Description
`ParticipateAssetIssueActuator.execute` (actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java:62-90) subtracts `cost` (the contract's `getAmount()`, i.e. TRX paid) from the owner's balance and credits it to the `toAddress` account — a real, on-chain TRX transfer of arbitrary attacker-chosen size. [1](#0-0) [2](#0-1) 

In contrast, `JsonRpcApiUtil.getTransactionAmount(Contract, String, TransactionInfo, Wallet)` has explicit amount extraction for `TransferContract` and `TransferAssetContract`, but for `ParticipateAssetIssueContract` the case body is empty (`break;` with no assignment), so `amount` stays at its initialized value of `0`. [3](#0-2) 

This value is used directly by `TransactionResult`'s constructors, which compute `value = ByteArray.toJsonHex(getTransactionAmount(contract, hash, wallet))` and expose it via the `value` getter returned in `eth_getTransactionByHash` JSON-RPC responses. [4](#0-3) [5](#0-4) 

An unprivileged attacker (or any user) can broadcast a valid `ParticipateAssetIssueContract` transaction with a nonzero `amount`, causing a real TRX transfer, then query `eth_getTransactionByHash` and observe `value: "0x0"` regardless of the actual TRX moved.

### Impact Explanation
Any tooling — exchanges, compliance/AML monitoring, wallets, or block explorers — that relies on the Ethereum-compatible `eth_getTransactionByHash` `value` field to detect TRX outflows will silently miss TRC10 asset-issue-participation purchases, since these always report `0x0` no matter the actual amount transferred. This is a reporting/accounting-invariant violation (RPC value must reflect actual value moved), not a fund-theft bug: the underlying ledger state (account balances) is correctly updated by the actuator; only the JSON-RPC surface misrepresents it. This allows an attacker to move TRX via this path while evading value-field-based monitoring or off-chain compliance triggers built on the JSON-RPC API.

### Likelihood Explanation
Fully feasible and repeatable: `ParticipateAssetIssueContract` is a standard TRC10 transaction type reachable by any account with sufficient TRX and no privileged setup beyond a pre-existing asset issue (asset issues themselves are public/permissionless in TRON). The gap is deterministic — every such transaction with nonzero `getAmount()` will always report `value=0x0` via `eth_getTransactionByHash`, with no dependency on race conditions or special timing.

### Recommendation
In `JsonRpcApiUtil.getTransactionAmount`, populate the `ParticipateAssetIssueContract` case by unpacking the contract and returning `getAmount()`, mirroring the `TransferContract`/`TransferAssetContract` cases:
```java
case ParticipateAssetIssueContract:
  amount = contractParameter.unpack(ParticipateAssetIssueContract.class).getAmount();
  break;
```

### Proof of Concept
```java
// Unit test in JsonRpcApiUtilTest (or TransactionResultTest)
@Test
public void testParticipateAssetIssueAmountNotZero() {
  long trxAmount = 100_000_000L; // 100 TRX
  ParticipateAssetIssueContract participateContract = ParticipateAssetIssueContract.newBuilder()
      .setOwnerAddress(ByteString.copyFrom(ownerAddress))
      .setToAddress(ByteString.copyFrom(issuerAddress))
      .setAssetName(ByteString.copyFromUtf8("testAsset"))
      .setAmount(trxAmount)
      .build();

  Transaction.Contract contract = Transaction.Contract.newBuilder()
      .setType(ContractType.ParticipateAssetIssueContract)
      .setParameter(Any.pack(participateContract))
      .build();

  long reportedAmount = JsonRpcApiUtil.getTransactionAmount(contract, "somehash", null, wallet);

  // Currently fails: reportedAmount == 0 instead of trxAmount
  Assert.assertEquals(trxAmount, reportedAmount);
}
```
Expected: `getTransactionAmount` (and consequently `TransactionResult.getValue()`) should return/encode `trxAmount`, not `0`. Currently the assertion fails because the switch branch is empty. [6](#0-5)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java (L62-69)
```java
      long cost = participateAssetIssueContract.getAmount();

      //subtract from owner address
      byte[] ownerAddress = participateAssetIssueContract.getOwnerAddress().toByteArray();
      AccountCapsule ownerAccount = accountStore.get(ownerAddress);
      long balance = subtractExact(ownerAccount.getBalance(), cost);
      balance = subtractExact(balance, fee);
      ownerAccount.setBalance(balance);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java (L82-87)
```java
      byte[] toAddress = participateAssetIssueContract.getToAddress().toByteArray();
      AccountCapsule toAccount = accountStore.get(toAddress);
      toAccount.setBalance(addExact(toAccount.getBalance(), cost));
      if (!toAccount.reduceAssetAmountV2(key, exchangeAmount, dynamicStore, assetIssueStore)) {
        throw new ContractExeException("reduceAssetAmount failed !");
      }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java (L252-278)
```java
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
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionResult.java (L106-123)
```java
    if (!tx.getRawData().getContractList().isEmpty()) {
      Contract contract = tx.getRawData().getContract(0);
      byte[] fromByte = capsule.getOwnerAddress();
      byte[] toByte = getToAddress(tx);

      if (blockCapsule.getNum() == 0) {
        from = ByteArray.toJsonHex(new byte[20]);
      } else {
        from = ByteArray.toJsonHexAddress(fromByte);
      }

      to = ByteArray.toJsonHexAddress(toByte);
      value = ByteArray.toJsonHex(getTransactionAmount(contract, hash, wallet));
    } else {
      from = ByteArray.toJsonHex(new byte[20]);
      to = ByteArray.toJsonHex(new byte[20]);
      value = "0x0";
    }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionResult.java (L141-152)
```java
    if (!tx.getRawData().getContractList().isEmpty()) {
      Contract contract = tx.getRawData().getContract(0);
      byte[] fromByte = capsule.getOwnerAddress();
      byte[] toByte = getToAddress(tx);
      from = ByteArray.toJsonHexAddress(fromByte);
      to = ByteArray.toJsonHexAddress(toByte);
      value = ByteArray.toJsonHex(getTransactionAmount(contract, hash, wallet));
    } else {
      from = ByteArray.toJsonHex(new byte[20]);
      to = ByteArray.toJsonHex(new byte[20]);
      value = "0x0";
    }
```
