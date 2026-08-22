### Title
MarketSellAssetActuator rejects fully-matching sell orders once an account's active order count reaches `MAX_ACTIVE_ORDER_NUM`, even though the new order would not add a resting order - (File: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
`MarketSellAssetActuator.validate()` unconditionally rejects any `MarketSellAssetContract` transaction if the sender's `MarketAccountOrderCapsule.getCount()` (number of currently active/resting orders) is already `>= MAX_ACTIVE_ORDER_NUM` (100), before knowing whether the incoming order will actually be added as a new resting order. This mirrors the KUMASwap `_maxCoupons` bug: a set/counter-size guard reverts the whole operation even when the specific item being added would not increase (or would even decrease) the counted quantity.

### Finding Description
In `validate()`: [1](#0-0) 
this check runs before any matching logic and simply compares the account's current active-order count to the cap.

In `execute()`, however, a new order is always created and counted first via `createAndSaveOrder`, which unconditionally increments `count`: [2](#0-1) 

Then `matchOrder`/`matchSingleOrder` may fully consume the taker order against existing resting (maker) orders, in which case the taker's state becomes `INACTIVE` via `MarketUtils.updateOrderState(takerOrderCapsule, State.INACTIVE, marketAccountStore)` calls at lines 411, 437, 449 — this call is what decrements the account's active order count back down (confirmed by the test `matchAll2SamePriceBuyOrders1`, where `accountOrderCapsule.getCount()` returns to 0 after a fully-matched sell, even though a new order was created and then matched away).

Because the `validate()` check happens strictly before this match/removal step, it cannot distinguish between:
- an order that will remain resting and genuinely add to the count (should be blocked at the cap), and
- an order that will be fully matched as a taker and therefore never actually increases the account's net active-order count beyond the cap (should be allowed, analogous to KUMASwap accepting a bond whose coupon already exists in the set).

The existing project-level protection against unbounded matching cost is `MAX_MATCH_NUM` (line 358, throws `ContractValidateException`/`ContractExeException` during execute if a match spans too many maker orders), so the system already anticipates that "the new order might resolve immediately without adding a resting entry" — but `validate()`'s active-order-count gate ignores this possibility entirely.

### Impact Explanation
Once an account's active order count reaches `MAX_ACTIVE_ORDER_NUM` (100), every subsequent `MarketSellAssetContract` transaction from that account reverts with `"Maximum number of orders exceeded，100"` — including ones that would fully match against the order book and thus never add a new resting order (i.e., orders that would not actually push the account over the limit). This is a functional DoS on the exchange/market feature for any account that legitimately reaches the resting-order cap: it cannot place liquidating/matching sell orders to reduce its own exposure, even though such orders are logically the ones that should always be allowed (they consume existing orders rather than adding new ones). This is reachable by any account via ordinary broadcast transactions (`MarketSellAssetContract`), requires no privileged access, and directly affects on-chain exchange functionality/accounting availability.

### Likelihood Explanation
Reaching `MAX_ACTIVE_ORDER_NUM` (100) resting orders on one account is achievable by any user simply by placing 100 non-matching sell orders (there is no fee or cost preventing this beyond the standard `getMarketSellFee()`), after which the account is permanently blocked from placing further sell orders regardless of whether they would match. This does not require a malicious counterparty or privileged role — it can be self-inflicted or triggered by normal usage, matching the "Medium" likelihood characterization the original KUMA report received (specific-but-plausible condition, not always occurring).

### Recommendation
Move (or duplicate) the active-order-count check to occur after determining whether the new order will remain on the book, or make the check conditional on whether the incoming order is guaranteed to end up resting. Concretely, defer the `MAX_ACTIVE_ORDER_NUM` enforcement to `execute()`, after `matchOrder` has run, and only reject/roll back if `orderCapsule.getSellTokenQuantityRemain() != 0` (i.e., the order will actually be persisted via `saveRemainOrder`) and the resulting count would exceed the limit — analogous to the KUMA fix of only reverting when the new item would actually increase the tracked set size.

### Proof of Concept
1. Account `A` places 100 sell orders for pair (X, Y) at prices that never match anything in the book, until `MarketAccountOrderCapsule.getCount()` for `A` reaches `MAX_ACTIVE_ORDER_NUM` (100), as exercised in `exceedMakerBuyOrderNumLimit`: [3](#0-2) 
2. Now account `A` (or, symmetrically, account `B` whose orders would be fully matched against pre-existing resting orders as shown in `matchAll2SamePriceBuyOrders1`) submits a new `MarketSellAssetContract` that would fully match against existing opposite-side orders and thus never rest on the book (its count would return to its pre-transaction value, as demonstrated by `accountOrderCapsule.getCount()` == 0 in `matchAll2SamePriceBuyOrders1`): [4](#0-3) 
3. Because `validate()` checks `getCount() >= MAX_ACTIVE_ORDER_NUM` unconditionally, this fully-matching, non-resting order is rejected with `"Maximum number of orders exceeded，100"` before matching is even attempted, denying an operation that would not have breached the cap.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L232-239)
```java
    // check order num
    MarketAccountOrderCapsule marketAccountOrderCapsule = marketAccountStore
        .getUnchecked(ownerAddress);
    if (marketAccountOrderCapsule != null
        && marketAccountOrderCapsule.getCount() >= MAX_ACTIVE_ORDER_NUM) {
      throw new ContractValidateException(
          "Maximum number of orders exceeded，" + MAX_ACTIVE_ORDER_NUM);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L501-525)
```java
  private MarketOrderCapsule createAndSaveOrder(AccountCapsule accountCapsule,
      MarketSellAssetContract contract) {
    MarketAccountOrderCapsule marketAccountOrderCapsule = marketAccountStore
        .getUnchecked(contract.getOwnerAddress().toByteArray());
    if (marketAccountOrderCapsule == null) {
      marketAccountOrderCapsule = new MarketAccountOrderCapsule(contract.getOwnerAddress());
    }

    // note: here use total_count
    byte[] orderId = MarketUtils
        .calculateOrderId(contract.getOwnerAddress(), sellTokenID, buyTokenID,
            marketAccountOrderCapsule.getTotalCount());
    MarketOrderCapsule orderCapsule = new MarketOrderCapsule(orderId, contract);

    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    orderCapsule.setCreateTime(now);

    marketAccountOrderCapsule.addOrders(orderCapsule.getID());
    marketAccountOrderCapsule.setCount(marketAccountOrderCapsule.getCount() + 1);
    marketAccountOrderCapsule.setTotalCount(marketAccountOrderCapsule.getTotalCount() + 1);
    marketAccountStore.put(accountCapsule.createDbKey(), marketAccountOrderCapsule);
    orderStore.put(orderId, orderCapsule);

    return orderCapsule;
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/MarketSellAssetActuatorTest.java (L494-536)
```java
  @Test
  public void exceedMakerBuyOrderNumLimit() throws Exception {

    InitAsset();

    //(sell id_1  and buy id_2)
    String sellTokenId = TOKEN_ID_ONE;
    long sellTokenQuant = 100L;
    String buyTokenId = TOKEN_ID_TWO;
    long buyTokenQuant = 200L;

    long orderNum = 100L;

    byte[] ownerAddress = ByteArray.fromHexString(OWNER_ADDRESS_FIRST);
    AccountCapsule accountCapsule = dbManager.getAccountStore().get(ownerAddress);
    accountCapsule.addAssetAmountV2(sellTokenId.getBytes(), sellTokenQuant * orderNum,
        dbManager.getDynamicPropertiesStore(), dbManager.getAssetIssueStore());
    dbManager.getAccountStore().put(ownerAddress, accountCapsule);
    Assert.assertEquals(sellTokenQuant * orderNum,
        (long) accountCapsule.getAssetV2MapForTest().get(sellTokenId));

    // Initialize the order book

    //add three order(sell id_2 and buy id_1) with different price by the same account
    //TOKEN_ID_TWO is twice as expensive as TOKEN_ID_ONE
    for (int i = 0; i < orderNum; i++) {
      addOrder(TOKEN_ID_ONE, sellTokenQuant, TOKEN_ID_TWO,
          buyTokenQuant, OWNER_ADDRESS_FIRST);
    }

    // do process
    MarketSellAssetActuator actuator = new MarketSellAssetActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract(
        OWNER_ADDRESS_FIRST, sellTokenId, sellTokenQuant, buyTokenId, buyTokenQuant));

    String errorMessage = "Maximum number of orders exceeded，100";
    try {
      actuator.validate();
      fail(errorMessage);
    } catch (ContractValidateException e) {
      Assert.assertEquals(errorMessage, e.getMessage());
    }
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/MarketSellAssetActuatorTest.java (L1364-1370)
```java
    //check accountOrder
    MarketAccountOrderCapsule accountOrderCapsule = marketAccountStore.get(ownerAddress);
    Assert.assertEquals(0, accountOrderCapsule.getCount());
    // ByteString orderId = accountOrderCapsule.getOrdersList().get(0);

    MarketAccountOrderCapsule makerAccountOrderCapsule = marketAccountStore.get(makerAddress);
    Assert.assertEquals(4, makerAccountOrderCapsule.getCount());
```
