### Title
`MarketPairPriceToOrderStore.getPriceKeysList` throws `IndexOutOfBoundsException` on attacker-controlled `count`, crashing `getMarketPriceByPair` RPC/HTTP requests - (File: `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java`)

### Summary
The public, unauthenticated `getMarketPriceByPair` RPC/HTTP endpoint calls `MarketPairPriceToOrderStore.getPriceKeysList(sellTokenId, buyTokenId, count)`, which internally does `getKeysNext(headKey, limit + 1).subList(1, (int)(limit + 1))` without checking whether the underlying key list actually contains `limit + 1` elements. Whenever the number of on-disk keys for the pair (starting at `headKey`) is smaller than `limit + 1` — which happens either because `count` exceeds the real number of existing orders, or because a concurrent `MarketCancelOrderActuator.execute()` removes the head/adjacent key mid-iteration — `subList` throws an unhandled `IndexOutOfBoundsException`.

### Finding Description
`Wallet.getMarketPriceByPair` forwards the attacker-supplied `count` straight into `MarketPairPriceToOrderStore.getPriceKeysList(byte[] sellTokenId, byte[] buyTokenId, long count)` [1](#0-0) , which delegates to the core routine:

```java
if (has(headKey)) {
  long limit = count > totalCount ? totalCount : count;
  if (skip) {
    result = getKeysNext(headKey, limit + 1).subList(1, (int)(limit + 1));
  } ...
}
``` [2](#0-1) 

The code only guards on `has(headKey)` (i.e., the pair exists at all), never on whether `getKeysNext` actually returned `limit + 1` items. `getKeysNext` is a thin wrapper around `revokingDB.getKeysNext(key, limit)` [3](#0-2) , which returns at most `limit` keys but only as many as actually exist in the DB from that point forward — it does not pad the result. Therefore:

- If an attacker calls `getMarketPriceByPair(sellTokenId, buyTokenId, count)` with `count` larger than the number of existing keys for that pair (a very common situation, since most trading pairs have few active orders), `getKeysNext` returns fewer than `limit + 1` elements, and `subList(1, limit+1)` immediately throws `IndexOutOfBoundsException`.
- Even with a "correct" `count`, a concurrent `MarketCancelOrderActuator.execute()` deleting the head order right between the `has(headKey)` check and the `getKeysNext` call can shrink the available key set below `limit + 1`, triggering the same exception via a race.

No signature, no funds, and no special permission are needed — `getMarketPriceByPair` is a read-only query endpoint reachable by any anonymous RPC/HTTP/JSON-RPC client, so none of the standard transaction-level protections (`validateSignature`, permission checks, actuator `validate()`, energy/bandwidth accounting) apply to this code path at all.

### Impact Explanation
This is a Denial-of-Service against the wallet/API service: an unauthenticated caller can repeatedly trigger uncaught `IndexOutOfBoundsException`s on the thread servicing `getMarketPriceByPair` requests, causing failed/500-style responses on every such call. This matches the "DoS via RPC-API" bounty impact class. It does not lead to fund loss, consensus divergence, or key disclosure — impact is scoped to availability of this specific query API.

### Likelihood Explanation
Highly likely/trivial to trigger: the attacker only needs to know or guess two existing token IDs for any market pair and send a `count` value greater than the number of currently resting orders for that pair (or repeatedly poll while races with `MarketCancelOrderActuator` occur). No cost (no fee, no signed transaction) is incurred since this is a read-only query call, and it is fully repeatable.

### Recommendation
In `MarketPairPriceToOrderStore.getPriceKeysList`, clamp/validate the size of the list returned by `getKeysNext` before calling `subList`, e.g.:
```java
List<byte[]> keys = getKeysNext(headKey, limit + 1);
result = keys.size() > 1 ? keys.subList(1, keys.size()) : Collections.emptyList();
```
and ensure the surrounding gRPC/HTTP handlers catch and translate any residual `RuntimeException` into a well-formed error response instead of propagating raw exceptions to the transport layer.

### Proof of Concept
```java
@Test
public void testGetPriceKeysListOutOfBounds() {
  // Setup: create a market pair with only 1 resting order (head only)
  MarketPairPriceToOrderStore store = ...;
  byte[] sellTokenId = ...;
  byte[] buyTokenId = ...;
  // only the head key exists in the store for this pair

  // Attacker calls with count > actual number of orders
  List<byte[]> result = store.getPriceKeysList(sellTokenId, buyTokenId, 5);
  // Expected (buggy): throws IndexOutOfBoundsException from subList(1, 6)
  // Expected (fixed): returns empty list or list smaller than requested
}
```
Raw RPC reproduction: broadcast repeated `getMarketPriceByPair(sellTokenId, buyTokenId, count=100)` gRPC/HTTP calls against a pair with fewer than 100 resting orders (virtually every live pair) and observe the server returning an internal error/exception instead of a graceful empty/partial list.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java (L30-36)
```java
  public List<byte[]> getKeysNext(byte[] key, long limit) {
    if (limit <= 0) {
      return Collections.emptyList();
    }

    return revokingDB.getKeysNext(key, limit);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java (L38-41)
```java
  public List<byte[]> getPriceKeysList(byte[] sellTokenId, byte[] buyTokenId, long count) {
    byte[] headKey = MarketUtils.getPairPriceHeadKey(sellTokenId, buyTokenId);
    return getPriceKeysList(headKey, count, count, true);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java (L50-61)
```java
  public List<byte[]> getPriceKeysList(byte[] headKey, long count, long totalCount, boolean skip) {
    List<byte[]> result = new ArrayList<>();

    if (has(headKey)) {
      long limit = count > totalCount ? totalCount : count;
      if (skip) {
        // need to get one more
        result = getKeysNext(headKey, limit + 1).subList(1, (int)(limit + 1));
      } else {
        result = getKeysNext(headKey, limit);
      }
    }
```
