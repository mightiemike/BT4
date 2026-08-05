### Title
Unbounded, order-preserving enumeration in `Wallet.getMarketPairList()` lets a low-cost attacker permanently starve visibility of legitimate trading pairs - (File: `framework/src/main/java/org/tron/core/services/http/GetMarketPairListServlet.java`)

### Summary
`GetMarketPairListServlet` calls `Wallet.getMarketPairList()`, which always iterates `MarketPairToPriceStore` from its lexicographically-smallest key and hard-stops after `MARKET_COUNT_LIMIT_MAX` entries, with no cursor, offset, or pagination support. Because `MarketPairToPriceStore` keys are the raw concatenation of `sellTokenId`/`buyTokenId` bytes (`MarketUtils.createPairKey`), an attacker who repeatedly submits cheap `MarketSellAssetContract` transactions with token-id byte prefixes that sort before real trading pairs can permanently fill the first `MARKET_COUNT_LIMIT_MAX` slots, making legitimate pairs created later unreachable through this API regardless of block height.

### Finding Description
`Wallet.getMarketPairList()` opens a fresh iterator over `marketPairToPriceStore` on every call and breaks once `count > MARKET_COUNT_LIMIT_MAX`, without any starting key, last-seen cursor, or randomization: [1](#0-0) 

`GetMarketPairListServlet.fillResponse` invokes this method directly and returns whatever it produces to any unauthenticated HTTP caller: [2](#0-1) 

The underlying store, `MarketPairToPriceStore`, is a `TronStoreWithRevoking` backed by LevelDB, whose `iterator()` returns entries in ascending byte order of the key. New pairs are inserted through `MarketSellAssetActuator.saveRemainOrder -> pairToPriceStore.addNewPriceKey`, which is reached from ordinary, unprivileged `MarketSellAssetContract` transactions whenever the resulting order does not fully match and a new price/pair key needs to be created: [3](#0-2) [4](#0-3) 

The pair key is simply `sellTokenId || buyTokenId` (each padded/copied into a fixed 19-byte slot), so an attacker fully controls the byte prefix that determines its sort position, since `sellTokenId`/`buyTokenId` only need to satisfy `MarketUtils.checkTokenValid` (either `"_"` for TRX or a numeric string) — there is no requirement tying the sort order to age, stake, or any other Sybil-resistant property: [5](#0-4) [6](#0-5) 

Each new pair costs only a fixed `getMarketSellFee()` regardless of trade size, as confirmed by the actuator's fee deduction logic and its unit test, which shows the entire cost of opening a new order/pair is `sellTokenQuant + marketSellFee`: [7](#0-6) [8](#0-7) 

Since `sellTokenQuantity` can be minimized (subject only to normal balance/precision checks) and the fee is a fixed flat cost unrelated to how many total pairs already exist or how "early" the new key sorts, there is no economic or rate-limiting guard preventing an attacker from creating `N > MARKET_COUNT_LIMIT_MAX` cheap, low-sorting pairs.

### Impact Explanation
Once an attacker occupies the first `MARKET_COUNT_LIMIT_MAX` (+1) keys of `MarketPairToPriceStore` with pairs that sort ahead of all real pairs, `wallet.getMarketPairList()` — exposed via `GetMarketPairListServlet`, the gRPC `getMarketPairList` handler, and the PBFT variant — will forever return only the attacker's spam pairs and never surface legitimate pairs added afterward, because the enumeration always restarts from byte-order position zero and stops at the same fixed count. This is a persistent denial of visibility for the public market discovery API affecting all callers (wallets, explorers, trading bots) that rely on this endpoint to discover active trading pairs, at negligible and fixed attacker cost per pair.

### Likelihood Explanation
Fully feasible with only an unprivileged account and TRX balance: the attacker needs `MARKET_COUNT_LIMIT_MAX` cheap sell orders (e.g., selling minimal TRX amounts, `sellTokenId = "_"`) using low-sorting `buyTokenId` numeric strings, each costing one fixed `getMarketSellFee()`. No governance, admin, or special privilege is required, and the attack is fully repeatable/replayable at will since the enumeration order is deterministic and attacker-controlled.

### Recommendation
Change `Wallet.getMarketPairList()` (and the analogous pair/order pagination methods) to support cursor-based or offset-based pagination keyed off client-supplied state rather than always starting from the smallest key, and/or decouple listing order from raw attacker-chosen token-id byte values (e.g., order by pair creation timestamp/insertion sequence or apply a Sybil-resistant admission cost such as scaling `marketSellFee` with the number of currently open, unmatched pairs per account).

### Proof of Concept
Java integration test outline (mirrors the pattern used in `MarketPairToPriceStoreTest`/`MarketSellAssetActuatorTest`):
1. Using `MarketSellAssetActuator`, submit `MARKET_COUNT_LIMIT_MAX` distinct `MarketSellAssetContract` transactions from an attacker account, each selling a small amount of TRX (`sellTokenId = "_"`) against distinct numeric `buyTokenId` values chosen to be lexicographically smaller than any legitimate token id used in the test (e.g., `"1"`, `"2"`, ... `"N"`), letting each remain unmatched so `saveRemainOrder` calls `pairToPriceStore.addNewPriceKey`.
2. Submit one additional legitimate `MarketSellAssetContract` from a different account using a `buyTokenId` guaranteed to sort after all spam pairs (e.g., a normal TRC10 id string that is lexicographically larger).
3. Call `wallet.getMarketPairList()` and assert:
   - `result.getOrderPairList()` size equals `MARKET_COUNT_LIMIT_MAX` (or `+1` per the off-by-one in the loop).
   - The legitimate pair (`sellTokenId/buyTokenId` from step 2) is **not** present in `result.getOrderPairList()`.
4. Repeat the call after advancing block height/time to confirm the legitimate pair remains permanently unreachable, demonstrating the persistent starvation.

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2883-2903)
```java
  public MarketOrderPairList getMarketPairList() {
    MarketOrderPairList.Builder builder = MarketOrderPairList.newBuilder();
    MarketPairToPriceStore marketPairToPriceStore = dbManager.getChainBaseManager()
        .getMarketPairToPriceStore();

    Iterator<Entry<byte[], BytesCapsule>> iterator = marketPairToPriceStore
        .iterator();
    long count = 0;
    while (iterator.hasNext()) {
      Entry<byte[], BytesCapsule> next = iterator.next();

      byte[] pairKey = next.getKey();
      builder.addOrderPair(MarketUtils.decodeKeyToMarketPairHuman(pairKey));
      count++;
      if (count > MARKET_COUNT_LIMIT_MAX) {
        break;
      }
    }

    return builder.build();
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetMarketPairListServlet.java (L45-53)
```java
  private void fillResponse(boolean visible, HttpServletResponse response)
      throws Exception {
    MarketOrderPairList reply = wallet.getMarketPairList();
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L108-132)
```java
    long fee = calcFee();

    try {
      final MarketSellAssetContract contract = this.any
          .unpack(MarketSellAssetContract.class);

      AccountCapsule accountCapsule = accountStore
          .get(contract.getOwnerAddress().toByteArray());

      sellTokenID = contract.getSellTokenId().toByteArray();
      buyTokenID = contract.getBuyTokenId().toByteArray();
      sellTokenQuantity = contract.getSellTokenQuantity();
      buyTokenQuantity = contract.getBuyTokenQuantity();
      MarketPrice takerPrice = MarketPrice.newBuilder()
          .setSellTokenQuantity(sellTokenQuantity)
          .setBuyTokenQuantity(buyTokenQuantity).build();

      // fee
      accountCapsule.setBalance(accountCapsule.getBalance() - fee);
      // add to blackhole address
      if (dynamicStore.supportBlackHoleOptimization()) {
        dynamicStore.burnTrx(fee);
      } else {
        adjustBalance(accountStore, accountStore.getBlackhole(), fee);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L572-594)
```java
  private void saveRemainOrder(MarketOrderCapsule orderCapsule)
      throws ItemNotFoundException {
    // add order into orderList
    byte[] pairPriceKey = MarketUtils.createPairPriceKey(
        sellTokenID,
        buyTokenID,
        sellTokenQuantity,
        buyTokenQuantity
    );

    MarketOrderIdListCapsule orderIdListCapsule = pairPriceToOrderStore.getUnchecked(pairPriceKey);
    if (orderIdListCapsule == null) {
      orderIdListCapsule = new MarketOrderIdListCapsule();

      // pairPriceKey not exists, increase price count:
      // if pair not exits, add token pair, set count = 1, add headKey to pairPriceToOrderStore
      // if pair exists, increase count
      pairToPriceStore.addNewPriceKey(sellTokenID, buyTokenID, pairPriceToOrderStore);
    }

    orderIdListCapsule.addOrder(orderCapsule, orderStore);
    pairPriceToOrderStore.put(pairPriceKey, orderIdListCapsule);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/MarketPairToPriceStore.java (L57-71)
```java
  public void addNewPriceKey(byte[] sellTokenId, byte[] buyTokenId,
      MarketPairPriceToOrderStore pairPriceToOrderStore) {
    long number;

    byte[] pairKey = MarketUtils.createPairKey(sellTokenId, buyTokenId);
    if (has(pairKey)) {
      number = getPriceNum(pairKey) + 1;
    } else {
      number = 1;
      byte[] headKey = MarketUtils.getPairPriceHeadKey(sellTokenId, buyTokenId);
      pairPriceToOrderStore.put(headKey, new MarketOrderIdListCapsule());
    }

    setPriceNum(pairKey, number);
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java (L224-229)
```java
  public static byte[] createPairKey(byte[] sellTokenId, byte[] buyTokenId) {
    byte[] result = new byte[TOKEN_ID_LENGTH * 2];
    System.arraycopy(sellTokenId, 0, result, 0, sellTokenId.length);
    System.arraycopy(buyTokenId, 0, result, TOKEN_ID_LENGTH, buyTokenId.length);
    return result;
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java (L306-323)
```java
  public static boolean checkTokenValid(byte[] tokenId) {
    if (!Arrays.equals("_".getBytes(), tokenId) && !TransactionUtil.isNumber(tokenId)) {
      return false;
    }

    return true;
  }

  public static void checkPairValid(byte[] sellTokenId, byte[] buyTokenId)
      throws BadItemException {
    if (!checkTokenValid(sellTokenId)) {
      throw new BadItemException("sellTokenId is not a valid number");
    }

    if (!checkTokenValid(buyTokenId)) {
      throw new BadItemException("buyTokenId is not a valid number");
    }
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/MarketSellAssetActuatorTest.java (L658-661)
```java
    //check balance
    accountCapsule = dbManager.getAccountStore().get(ownerAddress);
    Assert.assertEquals(balanceBefore, sellTokenQuant
        + dbManager.getDynamicPropertiesStore().getMarketSellFee() + accountCapsule.getBalance());
```
