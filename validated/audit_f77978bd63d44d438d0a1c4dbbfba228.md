### Title
Exchange creator can front-run/manipulate the bancor exchange rate between a taker's `expected` price commitment and settlement, defeating `ExchangeTransactionContract`'s slippage protection - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java])

### Summary
This is the closest reachable analog to the "gold cards" report: an admin/owner-controlled resource whose payout distribution (here, the bancor-style exchange rate of a TRX/TRC10 pair) can be altered by the privileged party (`exchangeCapsule.getCreatorAddress()`) between the moment an ordinary user commits to a trade and the moment it is actually settled on-chain, because the rate is computed from live pool balances at execution time rather than fixed at commit time.

### Finding Description
`ExchangeCreate`/`ExchangeInjectActuator`/`ExchangeWithdrawActuator` let the exchange **creator** (a privileged, but not root-admin, `owner_address`) unilaterally change the `firstTokenBalance`/`secondTokenBalance` of a bancor-style liquidity pool via `ExchangeInjectContract`/`ExchangeWithdrawContract`, validated only by `accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())` [1](#0-0) . Both inject and withdraw directly rewrite the pool ratio in the same block cadence a taker uses [2](#0-1) [3](#0-2) .

A normal user "commits" to a trade by broadcasting an `ExchangeTransactionContract` specifying `token_id`, `quant`, and `expected` (a minimum-received guard) [4](#0-3) . Both `validate()` and `execute()` of `ExchangeTransactionActuator` compute the actual output amount by calling `exchangeCapsule.transaction(...)` against the **current** `firstTokenBalance`/`secondTokenBalance` read from the store at the time each method runs [5](#0-4) [6](#0-5) . Nothing pins the pool ratio at the time the user's transaction is signed/broadcast — the distribution (exchange rate) is exactly analogous to the gold-card "distribution of possible outcomes" that the privileged owner is able to redefine post-commit via `registerIDs`/`deregisterIDs`. Here the exchange creator can call `ExchangeInject`/`ExchangeWithdraw` to shift the pool ratio arbitrarily (up to `dynamicStore.getExchangeBalanceLimit()`) before a pending user's `ExchangeTransactionContract` executes, changing how much the user actually receives, or forcing `validate()` to fail (revert-on-worse-price) after the user's balance/asset reservation was already computed against the old ratio. This mirrors the "owner can mint any specific card by deleting unwanted ones then restoring them" pattern — here the creator can inject/withdraw before the taker's trade to steer the settlement price, then reverse the injection immediately after, extracting value from the taker (a sandwich/JIT-liquidity attack), all through explicit, unprivileged-role-adjacent (creator, not chain-root) actuators rather than any theoretical or mocked-only path.

### Impact Explanation
A malicious or compromised exchange creator can economically front-run/sandwich any pending trade against their pool: inject liquidity to shift price before a large buy executes, letting the taker receive far less of `anotherTokenID` than they expected when they signed the transaction (bounded only by the `expected` field, which the taker sets based on the price at broadcast time, itself now stale by the time of settlement), then withdraw the same liquidity back out afterward, capturing the taker's slippage as profit. This is a direct value-extraction / unfair-settlement impact on accounting (`accountCapsule.addAssetAmountV2`/`setBalance` results are skewed) affecting ordinary unprivileged users trading through `ExchangeTransactionContract`.

### Likelihood Explanation
Any account can create an exchange pool via `ExchangeCreateContract` and thus become its "creator," making this reachable by any actor who also participates as a market maker — no special chain-level admin privilege beyond `creatorAddress` is required. It requires transaction ordering control within the same block or careful mempool timing (front-running), which is realistic for pool creators observing pending trades against their own pool. This is comparable in trust class to the original finding (a permissioned-but-not-root owner over a specific resource), matching the "unprivileged-user-reachable, privileged-resource-owner" scope of this analysis.

### Recommendation
- Allow takers to pin the exchange rate/pool state at broadcast time (e.g., commit-reveal or an on-chain "quote" reference block) so a stale price cannot be exploited.
- Rate-limit or timelock `ExchangeInjectContract`/`ExchangeWithdrawContract` so pool ratio changes cannot occur within the same block as pending `ExchangeTransactionContract`s.
- Consider enforcing a maximum single-block price-impact bound for inject/withdraw operations, and/or require the `expected` field to be computed relative to a delay-protected price oracle rather than raw pool state readable/writable by the same creator.

### Proof of Concept
1. Creator sets up an exchange pool `X:Y` with balances `(Bx, By)` via `ExchangeCreateContract`.
2. Attacker/creator observes a pending `ExchangeTransactionContract` from Alice selling `qty` of `X` with `expected` computed off `(Bx, By)`.
3. Before Alice's transaction is included, the creator submits `ExchangeInjectContract` shifting the pool to `(Bx', By')`, which changes the bancor rate used by `exchangeCapsule.transaction(...)` in `ExchangeTransactionActuator.execute()` [7](#0-6) .
4. Alice's trade executes against the manipulated pool: she still clears the `expected` floor (set loosely, or the creator times injection precisely) but receives materially less `anotherTokenID` than the pre-injection price implied.
5. The creator submits `ExchangeWithdrawContract` immediately after to restore/rebalance the pool and pocket the price-impact spread extracted from Alice's trade, following the exact same validation path (`accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())`) that permitted step 3 [8](#0-7) .

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L71-83)
```java
      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
        anotherTokenQuant = floorDiv(multiplyExact(
            secondTokenBalance, tokenQuant), firstTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, tokenQuant),
            addExact(secondTokenBalance, anotherTokenQuant));
      } else {
        anotherTokenID = firstTokenID;
        anotherTokenQuant = floorDiv(multiplyExact(
            firstTokenBalance, tokenQuant), secondTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, anotherTokenQuant),
            addExact(secondTokenBalance, tokenQuant));
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L175-177)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L77-89)
```java
      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
        anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance).longValueExact();
        exchangeCapsule.setBalance(subtractExact(firstTokenBalance, tokenQuant),
            subtractExact(secondTokenBalance, anotherTokenQuant));
      } else {
        anotherTokenID = firstTokenID;
        anotherTokenQuant = bigFirstTokenBalance.multiply(bigTokenQuant)
            .divide(bigSecondTokenBalance).longValueExact();
        exchangeCapsule.setBalance(subtractExact(firstTokenBalance, anotherTokenQuant),
            subtractExact(secondTokenBalance, tokenQuant));
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L181-183)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** Tron protobuf protocol document.md (L1422-1440)
```markdown
     - message `ExchangeTransactionContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to sell.
    
       `quant`: token amount to sell.
    
       `expected`: expected minimum number of tokens.
    
      ```java
      message ExchangeTransactionContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
          int64 expected = 5;
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L57-69)
```java
      ExchangeCapsule exchangeCapsule = Commons
          .getExchangeStoreFinal(dynamicStore, exchangeStore, exchangeV2Store)
          .get(ByteArray.fromLong(exchangeTransactionContract.getExchangeId()));

      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L216-221)
```java

    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
