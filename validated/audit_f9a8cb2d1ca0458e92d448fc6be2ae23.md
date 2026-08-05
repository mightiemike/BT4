### Title
Order-book "poison" flooding forces legitimate `MarketSellAssetContract` trades to abort via `MAX_MATCH_NUM` — ([File: actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java])

### Summary
`MarketSellAssetActuator` implements java-tron's native (TRC10) on-chain order book. When a taker order is submitted, it walks the best-priced maker orders and fills them until the taker quantity is exhausted, but it hard-fails the *entire* transaction if more than `MAX_MATCH_NUM` (20) maker orders are consumed in the process. This is conceptually the same "poison order blocks market trades" bug class described in the external report (an inexpensive, attacker-controlled condition that forces an otherwise-fillable market order to revert), except the mechanism is an internal match-count limit rather than external gas-griefing, since java-tron's native market matching does not invoke arbitrary external contracts. An attacker can cheaply seed the order book with more than 20 small resting orders at the best price(s) for a trading pair, causing any subsequent taker order that would need to sweep across them to always throw and fail, effectively "poisoning" that market and blocking legitimate trades.

### Finding Description
`MarketSellAssetActuator.matchOrder()` iterates maker orders at the best matching price(s) and calls `matchSingleOrder()` for each one, incrementing `matchOrderCount`. If the taker order needs to consume more than `MAX_MATCH_NUM` maker orders to satisfy its quantity, the method throws `ContractValidateException`: [1](#0-0) 

This exception is raised from *inside* `execute()` (not `validate()`), where it is caught alongside other checked exceptions and re-thrown as `ContractExeException`, marking the whole transaction as failed: [2](#0-1) 

The `MAX_MATCH_NUM` constant is a static, hardcoded 20 for every transaction, and cannot be tuned per-order by the taker: [3](#0-2) 

Placing a resting order only requires ownership of a small balance and payment of the flat `getMarketSellFee()`, and the per-account limit is `MAX_ACTIVE_ORDER_NUM = 100`, which comfortably allows more than 20 orders to be parked at (or better than) the best price for a given pair: [4](#0-3) 

This mirrors the reported bug class: a market/exchange function that iterates over an attacker-influenced set of counterparties and aborts the whole transaction when a certain per-order-processing budget is exceeded, rather than gracefully skipping/partially filling and continuing. In the 0x report, the budget is "gas"; here it is "number of matched orders." Both let an unprivileged party who is not a party to the taker's trade force it to revert, at comparatively low cost to the attacker versus the disruption caused to any counterparty market order that needs to fill more than the fixed threshold.

### Impact Explanation
If a taker submits a `MarketSellAssetContract` that would legitimately need to consume more than 20 resting orders at the best price(s) to fill its requested quantity (e.g., because the market has many small maker orders at the best price — deliberately or organically), the entire transaction reverts with `ContractExeException`, and the taker's order/trade never executes. An attacker can deliberately create this condition by seeding 21+ tiny maker orders at the most attractive price for a token pair, which:
- Costs the attacker only `MAX_ACTIVE_ORDER_NUM`-bounded per-order fees and minimal token balance for the small resting orders.
- Reliably blocks any taker's larger market order that would need to sweep through the poisoned price level(s), denying the taker the ability to trade at all via that pair (or requiring them to manually construct smaller orders, degrading UX/liveness of the on-chain market).
This is a concrete halt/denial-of-service against the market/exchange feature reachable by any unprivileged account, matching the "invalid-state/halt" impact category (blocking market trades) called for in the analog rules.

### Likelihood Explanation
Likelihood is moderate. Exploitation requires the attacker to place more than `MAX_MATCH_NUM` (20) resting orders at the best price tier(s) for a targeted token pair, which is within the `MAX_ACTIVE_ORDER_NUM` (100) per-account limit and can be trivially split across a handful of accounts if needed. The attack requires no privileged role, no external contract execution, and works purely through the public `MarketSellAssetContract`/`MarketBuyAssetContract` interface, provided `dynamicStore.supportAllowMarketTransaction()` is enabled on the chain. The main friction is the aggregate market fee for creating the poison orders and the token amounts needed for each tiny resting order, both of which are attacker-controlled and can be made arbitrarily small.

### Recommendation
Instead of throwing and aborting the whole transaction when `matchOrderCount` exceeds `MAX_MATCH_NUM`, the matching loop should stop matching further orders and commit the partial fill achieved so far (similar to `_fillOrderNoThrow` semantics recommended in the source report — degrade gracefully rather than revert). Alternatively/additionally, allow the taker to specify (or bound) the maximum number of orders they're willing to match, and consider increasing or making `MAX_MATCH_NUM` a tunable chain parameter, and rate-limit/charge increasing fees for placing many small orders at the same price tier to raise the cost of order-book poisoning.

### Proof of Concept
1. Enable market transactions on a test chain (`supportAllowMarketTransaction`).
2. Attacker account(s) submit 21 `MarketSellAssetContract` orders selling `TOKEN_B` for `TOKEN_A` at the best available price with minimal quantities (staying within `MAX_ACTIVE_ORDER_NUM` per account or splitting across accounts).
3. A victim/legitimate user submits a `MarketSellAssetContract` selling `TOKEN_A` for `TOKEN_B`, sized such that filling it requires matching against more than 20 resting orders at the best price.
4. Observe `MarketSellAssetActuator.matchOrder()` incrementing `matchOrderCount` past `MAX_MATCH_NUM` and throwing `ContractValidateException("Too many matches. MAX_MATCH_NUM = 20")`, which propagates as `ContractExeException` and fails the victim's transaction, exactly as demonstrated by the match-count check at: [5](#0-4) 

**Note on verification limits:** I was unable to fully trace, within tool-call limits, whether `Manager.java`'s transaction-processing path fully reverts *all* store writes performed earlier inside the same `execute()` call (e.g., the taker's own balance/asset debit and order creation) when `ContractExeException` propagates, versus only marking the result as `FAILED` while certain writes persist. This affects whether the poisoned transaction merely fails cleanly (pure DoS) or additionally causes partial state inconsistency for the victim. Confirming this would require deeper inspection of `Manager.java`'s revoking-database/session handling, which is recommended before finalizing severity.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L59-76)
```java
@Slf4j(topic = "actuator")
public class MarketSellAssetActuator extends AbstractActuator {

  @Getter
  @Setter
  private static int MAX_ACTIVE_ORDER_NUM = 100;
  @Getter
  private static int MAX_MATCH_NUM = 20;

  private AccountStore accountStore;
  private DynamicPropertiesStore dynamicStore;
  private AssetIssueStore assetIssueStore;
  private AssetIssueV2Store assetIssueV2Store;

  private MarketAccountStore marketAccountStore;
  private MarketOrderStore orderStore;
  private MarketPairToPriceStore pairToPriceStore;
  private MarketPairPriceToOrderStore pairPriceToOrderStore;
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L152-159)
```java
    } catch (ItemNotFoundException
        | InvalidProtocolBufferException
        | BalanceInsufficientException
        | ContractValidateException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    }
```

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

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L342-359)
```java
      while (takerCapsule.getSellTokenQuantityRemain() != 0
          && !orderIdListCapsule.isOrderEmpty()) {
        byte[] orderId = orderIdListCapsule.getHead();
        MarketOrderCapsule makerOrderCapsule = orderStore.get(orderId);

        matchSingleOrder(takerCapsule, makerOrderCapsule, ret, takerAccountCapsule);

        // remove order
        if (makerOrderCapsule.getSellTokenQuantityRemain() == 0) {
          // remove from market order list
          orderIdListCapsule.removeOrder(makerOrderCapsule, orderStore,
              pairPriceKey, pairPriceToOrderStore);
        }

        matchOrderCount++;
        if (matchOrderCount > MAX_MATCH_NUM) {
          throw new ContractValidateException("Too many matches. MAX_MATCH_NUM = " + MAX_MATCH_NUM);
        }
```
