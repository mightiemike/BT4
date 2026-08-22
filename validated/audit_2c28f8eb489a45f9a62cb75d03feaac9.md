[1](#0-0)

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/MarketOrderIdListCapsule.java (L244-263)
```java
  public List<MarketOrderCapsule> getAllOrder(MarketOrderStore orderStore, long limit)
      throws ItemNotFoundException {

    List<MarketOrderCapsule> result = new ArrayList<>();

    long count = 0;
    byte[] orderId = this.getHead();
    if (!ByteArray.isEmpty(orderId)) {
      MarketOrderCapsule makerOrderCapsule = orderStore.getUnchecked(orderId);
      while (makerOrderCapsule != null) {
        result.add(makerOrderCapsule);
        makerOrderCapsule = makerOrderCapsule.getNextCapsule(orderStore);
        count++;
        if (count > limit) {
          break;
        }
      }
    }
    return result;
  }
```
