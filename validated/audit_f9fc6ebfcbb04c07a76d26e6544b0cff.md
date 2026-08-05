I have enough evidence to construct the analog analysis. The java-tron `Exchange` module is a Bancor-formula AMM (analogous to the Reserve contract), transactions are broadcast over the public P2P network before block confirmation (analogous to Ethereum's mempool), and there is a partial slippage-protection mechanism (`expected`) but it is optional and does not fully mitigate the described front-running/sandwich pattern.### Title
Front-running / sandwich attack on the Bancor-formula `Exchange` (TRC10↔TRX & TRC10↔TRC10 AMM) enables risk-free profit extraction from ordinary traders - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java])

### Summary
java-tron implements an on-chain automated-market-maker ("Bancor" relay) for TRX/TRC10 pairs via the `Exchange`/`ExchangeV2` mechanism. Pricing is computed dynamically from the pool's current `firstTokenBalance`/`secondTokenBalance` at the moment each `ExchangeTransactionContract` executes [1](#0-0) , exactly mirroring the "Reserve" bonding-curve contract described in the report. Because TRON transactions are gossiped across the P2P network (`AdvService.fastBroadcastTransaction`, `TransactionsMsgHandler.handleTransaction`) and sit in a visible pending pool before a Super Representative includes them in a block [2](#0-1) [3](#0-2) , and pending transactions are even queryable via `getTransactionListFromPending`/`getTransactionFromPending` RPCs [4](#0-3) , an attacker can observe a large pending `ExchangeTransactionContract` before it is confirmed and front-run/back-run it to extract value from the trade, precisely as in the Bob/Eve scenario from the report.

### Finding Description
The core pricing logic lives in `ExchangeCapsule.transaction()`, which reads the exchange's current on-chain balances and calls a Bancor relay formula (`ExchangeProcessor`/`SafeExchangeProcessor`) to compute the counter-asset amount for a trade [5](#0-4) . The formula is path-dependent: the price a trader receives depends entirely on the pool balances that exist at the moment their transaction is executed, which in turn depends on transaction ordering within a block/producing window — something an unprivileged attacker can influence by observing the mempool and submitting their own transaction to be ordered immediately before (and/or after) the victim's large trade.

`ExchangeTransactionActuator.execute()` performs the trade unconditionally based on whatever pool state exists at execution time [6](#0-5) . The only guard against adverse price movement is the optional `expected` (minimum-output) field checked in `doValidate()`: [7](#0-6) 

This is a slippage floor, not a defense against the race itself: it does not prevent an attacker from pre-trading ahead of a known pending large buy (moving the price against the victim, forcing them to receive fewer tokens than they would in isolation) and then reversing the position immediately afterward to lock in the price impact the victim caused, extracting the difference. A victim who sets a loose/absent (`getExpected()<=0` is the only floor enforced) or minimal `expected` value — which is the common case since predicting exact price impact off-chain is nontrivial for a Bancor curve — remains fully exposed. This reproduces the exact "Bob and Eve" mechanics from the report: the attacker's profit comes from knowing a large trade is about to shift the reserve balances and inserting themselves in the ordering before it lands.

Unlike Ethereum where miners/attackers control ordering via gas price, in java-tron the ordering power sits partly with the block-producing Super Representative and partly with network propagation speed, but the fundamental precondition from the report — "all transactions appear on the network before being accepted, allowing observation and reaction" — holds identically here: transactions are relayed peer-to-peer and visible via `getTransactionFromPending`/`getTransactionListFromPending` before block inclusion [8](#0-7) , and `maxTransactionPendingSize`/`pendingTransactionTimeout` config confirms a persistent pending pool window during which observation and reaction are possible [9](#0-8) .

### Impact Explanation
This is a genuine accounting/settlement impact in the market/exchange domain: unprivileged users lose economic value (worse execution price than the isolated bonding-curve math implies) to an attacker who contributes no liquidity and takes no directional risk, purely by exploiting visibility of pending trades and transaction-ordering non-determinism. This matches the report's "steal ethers" impact class applied to TRX/TRC10 value inside the `Exchange` mechanism. It does not require any privileged role — any network participant capable of submitting a competing `ExchangeTransactionContract` can attempt it.

### Likelihood Explanation
The `Exchange` feature is a long-standing, generally-available production feature (`ExchangeCreateContract`, `ExchangeInjectContract`, `ExchangeTransactionContract`, `ExchangeWithdrawContract`), and the underlying precondition — mempool-visible pending transactions and producer-controlled ordering — is inherent to the network's design rather than a bug that must be separately triggered. Any sizable trade against a shallow pool is a viable target, making exploitation practically feasible for a motivated actor with fast transaction submission/relay, though the profitability window (deep-enough imbalance vs. gas/bandwidth cost) is variable, similar to the difficulty the original auditors noted.

### Recommendation
Short term, document this behavior prominently in the `Exchange` API/wallet docs so integrators know minimum-output (`expected`) protection is advisory-only against slippage, not against ordering-based extraction, and encourage setting tight `expected` bounds and splitting large trades. Longer term, consider mechanisms that reduce the value of ordering knowledge for AMM-style trades — e.g., commit-reveal or batch/uniform-price auction execution for `ExchangeTransactionContract` within a block, or partitioning large trades automatically — so that pricing does not depend on intra-block ordering that any peer can observe and react to.

### Proof of Concept
1. Attacker monitors the P2P transaction relay / `getTransactionListFromPending` RPC and observes a large pending `ExchangeTransactionContract` (analogous to "Bob's 100 TRX buy") for a given `exchangeId` with token balances `firstTokenBalance`/`secondTokenBalance`.
2. Attacker submits their own smaller `ExchangeTransactionContract` for the same `exchangeId`, buying the same asset the victim is about to buy, with a fee/priority likely to land immediately before the victim's transaction is confirmed.
3. Because `ExchangeCapsule.transaction()` prices trades off the live pool balances [5](#0-4) , the attacker acquires tokens at the pre-impact price, then the victim's large trade executes and shifts the price further in the attacker's favor.
4. Attacker submits an `ExchangeWithdrawContract`/reverse `ExchangeTransactionContract` immediately after, selling back into the now price-shifted pool, realizing the price-impact profit the victim's trade caused — with `ExchangeTransactionActuator.doValidate()`'s `expected` check only failing if the victim explicitly set a tight minimum output [7](#0-6) .

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-169)
```java
  public long transaction(byte[] sellTokenID, long sellTokenQuant, boolean useStrictMath,
      boolean hardenedCalc) throws ContractValidateException {
    long supply = 1_000_000_000_000_000_000L;
    Processor processor = hardenedCalc
        ? SafeExchangeProcessor.INSTANCE : new ExchangeProcessor(supply, useStrictMath);

    long buyTokenQuant = 0;
    long firstTokenBalance = this.exchange.getFirstTokenBalance();
    long secondTokenBalance = this.exchange.getSecondTokenBalance();
    long newFirstTokenBalance;
    long newSecondTokenBalance;

    if (this.exchange.getFirstTokenId().equals(ByteString.copyFrom(sellTokenID))) {
      buyTokenQuant = processor.exchange(firstTokenBalance,
          secondTokenBalance,
          sellTokenQuant);
      newFirstTokenBalance = hardenedCalc
          ? StrictMathWrapper.addExact(firstTokenBalance, sellTokenQuant)
          : firstTokenBalance + sellTokenQuant;
      newSecondTokenBalance = hardenedCalc
          ? StrictMathWrapper.subtractExact(secondTokenBalance, buyTokenQuant)
          : secondTokenBalance - buyTokenQuant;

    } else {
      buyTokenQuant = processor.exchange(secondTokenBalance,
          firstTokenBalance,
          sellTokenQuant);
      newFirstTokenBalance = hardenedCalc
          ? StrictMathWrapper.subtractExact(firstTokenBalance, buyTokenQuant)
          : firstTokenBalance - buyTokenQuant;
      newSecondTokenBalance = hardenedCalc
          ? StrictMathWrapper.addExact(secondTokenBalance, sellTokenQuant)
          : secondTokenBalance + sellTokenQuant;

    }

    if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
      throw new ContractValidateException("Exchange balance must be >=0 after transaction");
    }
    this.exchange = this.exchange.toBuilder()
        .setFirstTokenBalance(newFirstTokenBalance)
        .setSecondTokenBalance(newSecondTokenBalance)
        .build();

    return buyTokenQuant;
  }
```

**File:** framework/src/main/java/org/tron/core/net/service/adv/AdvService.java (L159-191)
```java
  public int fastBroadcastTransaction(TransactionMessage msg) {

    List<PeerConnection> peers = tronNetDelegate.getActivePeer().stream()
            .filter(peer -> !peer.isNeedSyncFromPeer() && !peer.isNeedSyncFromUs())
            .collect(Collectors.toList());

    if (peers.size() == 0) {
      logger.warn("Broadcast transaction {} failed, no connection", msg.getMessageId());
      return 0;
    }

    Item item = new Item(msg.getMessageId(), InventoryType.TRX);
    trxCount.add();
    trxCache.put(item, new TransactionMessage(msg.getTransactionCapsule().getInstance()));

    List<Sha256Hash> list = new ArrayList<>();
    list.add(msg.getMessageId());
    InventoryMessage inventoryMessage = new InventoryMessage(list, InventoryType.TRX);

    int peersCount = 0;
    for (PeerConnection peer: peers) {
      if (peer.getAdvInvReceive().getIfPresent(item) == null
              && peer.getAdvInvSpread().getIfPresent(item) == null) {
        peersCount++;
        peer.getAdvInvSpread().put(item, Time.getCurrentMillis());
        peer.sendMessage(inventoryMessage);
      }
    }
    if (peersCount == 0) {
      logger.warn("Broadcast transaction {} failed, no peers", msg.getMessageId());
    }
    return peersCount;
  }
```

**File:** framework/src/main/java/org/tron/core/net/messagehandler/TransactionsMsgHandler.java (L173-187)
```java
  private void handleTransaction(PeerConnection peer, TransactionMessage trx) {
    if (peer.isBadPeer()) {
      logger.warn("Drop trx {} from {}, peer is bad peer", trx.getMessageId(),
          peer.getInetAddress());
      return;
    }

    if (advService.getMessage(new Item(trx.getMessageId(), InventoryType.TRX)) != null) {
      return;
    }

    try {
      trx.getTransactionCapsule().checkExpiration(chainBaseManager.getNextBlockSlotTime());
      tronNetDelegate.pushTransaction(trx.getTransactionCapsule());
      advService.broadcast(trx);
```

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L2630-2646)
```java
    @Override
    public void getTransactionFromPending(BytesMessage request,
        StreamObserver<Transaction> responseObserver) {
      getTransactionFromPendingCommon(request, responseObserver);
    }

    @Override
    public void getTransactionListFromPending(EmptyMessage request,
        StreamObserver<TransactionIdList> responseObserver) {
      getTransactionListFromPendingCommon(request, responseObserver);
    }

    @Override
    public void getPendingSize(EmptyMessage request,
        StreamObserver<NumberMessage> responseObserver) {
      getPendingSizeCommon(request, responseObserver);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-99)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());

      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
      } else {
        anotherTokenID = firstTokenID;
      }

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());
      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, tokenQuant));
      } else {
        accountCapsule.reduceAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .addAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }

      accountStore.put(accountCapsule.createDbKey(), accountCapsule);

      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);

      ret.setExchangeReceivedAmount(anotherTokenQuant);
      ret.setStatus(fee, code.SUCESS);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** common/src/main/resources/reference.conf (L358-360)
```text
  # TCP and transaction limits
  maxTransactionPendingSize = 2000
  pendingTransactionTimeout = 60000 # Pending transaction timeout (ms).
```
