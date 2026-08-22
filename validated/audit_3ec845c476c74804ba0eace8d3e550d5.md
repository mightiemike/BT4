### Title
Front-Running Griefing of `ExchangeWithdraw` Redemption via Ratio Manipulation in `ExchangeWithdrawActuator` - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
`ExchangeWithdrawActuator.doValidate()` re-derives the counter-asset amount (`anotherTokenQuant`) and a strict "0.01% precision" check purely from the **current, mutable** on-chain pool balances (`firstTokenBalance`/`secondTokenBalance`) of the target `Exchange` at the moment the transaction is executed, rather than from any value fixed by the requester. Because any unprivileged account can shift that ratio in a preceding transaction (`ExchangeTransactionActuator`, a public buy/sell operation, or `ExchangeInjectActuator`), an attacker can front-run a legitimate `ExchangeWithdraw` to push the recomputed ratio just far enough that the withdrawal's own precision check fails, reverting the victim's redemption — the same "dust manipulation causes revert of a legitimate exit" pattern described in the `Lender::redeem()` report.

### Finding Description
In `doValidate()`: [1](#0-0) 
the actuator reads `firstTokenBalance`/`secondTokenBalance` fresh from the `ExchangeCapsule` store at validation time (i.e., whatever state exists in the block/mempool position the transaction lands in), then computes: [2](#0-1) 
The `remainder`/`0.0001` tolerance check is a pure function of `firstTokenBalance`, `secondTokenBalance`, and the requester's `tokenQuant` — none of which are pinned by the transaction to a value the user actually observed off-chain when constructing the withdraw. Both pool balances are freely and cheaply manipulable pre-confirmation by any account via `ExchangeTransactionActuator` (public swap, no special permission — confirmed by tests where a non-creator account, `OWNER_ADDRESS_SECOND`, successfully trades against the pool) [3](#0-2)  or via `ExchangeInjectActuator`, which similarly recomputes `anotherTokenQuant` from live balances [4](#0-3) .

By observing a pending `ExchangeWithdrawContract` in the mempool (visible via gRPC/broadcast, exactly as with any TRX front-running), an attacker submits a small `ExchangeTransactionContract`/`ExchangeInjectContract` with a higher energy price to land first. This slightly changes `firstTokenBalance`/`secondTokenBalance`, which recalculates `anotherTokenQuant` for the pending withdraw to a value where the truncated-vs-4-decimal rounding difference now exceeds the `0.0001` relative tolerance, causing `doValidate()` to throw `"Not precise enough"` and the victim's `ExchangeWithdrawContract` to be rejected — exactly the mechanism of the external report, where a dust deposit tips a boundary check (`cache.totalSupply == 0 || > 1e5`) that a legitimate redeemer relies on.

### Impact Explanation
This is a griefing/Denial-of-Service vector against `ExchangeWithdraw` (an on-chain, unprivileged, broadcast-reachable actuator transition): a legitimate exchange creator attempting to withdraw liquidity can have their transaction repeatedly reverted by a griefer who front-runs with cheap trades, wasting the victim's fee/energy and delaying or blocking their exit from the exchange pool. It does not directly steal funds, but it degrades availability/integrity of the redemption path and can be repeated indefinitely to keep a specific creator from ever successfully withdrawing at a chosen `tokenQuant`.

### Likelihood Explanation
Likelihood is moderate: it requires (1) visibility of the pending withdraw transaction (available via standard mempool/broadcast visibility in java-tron), and (2) the withdraw's `tokenQuant` being close enough to a rounding boundary that a small ratio shift crosses the `0.0001` tolerance — this is easiest when `anotherTokenQuant` is small relative to pool depth, which is common for smaller/less liquid exchanges. No privileged role or leaked key is needed; the attacker only needs an ordinary funded account to submit `ExchangeTransactionContract`/`ExchangeInjectContract`.

### Recommendation
Decouple the precision safety check from live, attacker-influenceable state at validate-time:
- Allow the withdrawer to specify an explicit `expected`/`slippage` bound for `anotherTokenQuant` (similar to `ExchangeTransactionContract.expected`) so the contract itself commits to the acceptable range, rather than deriving tolerance purely from whatever balances happen to exist at execution time.
- Alternatively/additionally, relax or remove the internal `"Not precise enough"` self-consistency check when it is not protecting against an actual security invariant (it currently only detects internal rounding-method disagreement, not user intent), or widen it to be independent of pool depth fluctuations caused by intervening trades.

### Proof of Concept
1. Exchange pool has `firstTokenBalance = F`, `secondTokenBalance = S` such that a victim's planned `ExchangeWithdrawContract(tokenID=first, quant=Q)` computes `anotherTokenQuant` with a rounding difference just under the `0.0001` relative threshold in `ExchangeWithdrawActuator.doValidate()` (lines 218-243).
2. Victim (the exchange creator) broadcasts the withdraw transaction.
3. Attacker observes it in the mempool and broadcasts an `ExchangeTransactionContract` (small buy/sell, callable by any account per `ExchangeTransactionActuatorTest`) with higher fee/priority so it lands first, mutating `F`/`S` in the `ExchangeCapsule` via `ExchangeCapsule.transaction()`.
4. When the victim's withdraw is then validated against the new `F'`/`S'`, the recomputed `remainder` in `ExchangeWithdrawActuator.doValidate()` now exceeds `anotherTokenQuant * 0.0001`, throwing `ContractValidateException("Not precise enough")` and reverting the victim's redemption, despite it being a logically valid withdrawal against the pool's true liquidity.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L185-191)
```java
    byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
    byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
    long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
    long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

    byte[] tokenID = contract.getTokenId().toByteArray();
    long tokenQuant = contract.getQuant();
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L218-243)
```java
    if (Arrays.equals(tokenID, firstTokenID)) {
      anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
          .divideToIntegralValue(bigFirstTokenBalance).longValueExact();
      if (firstTokenBalance < tokenQuant || secondTokenBalance < anotherTokenQuant) {
        throw new ContractValidateException("exchange balance is not enough");
      }

      if (anotherTokenQuant <= 0) {
        throw new ContractValidateException("withdraw another token quant must greater than zero");
      }
      if (allowHarden) {
        BigDecimal remainder = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance, 4, RoundingMode.HALF_UP)
            .subtract(BigDecimal.valueOf(anotherTokenQuant));
        if (remainder.compareTo(
            BigDecimal.valueOf(anotherTokenQuant).multiply(new BigDecimal("0.0001"))) > 0) {
          throw new ContractValidateException("Not precise enough");
        }
      } else {
        double remainder = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance, 4, BigDecimal.ROUND_HALF_UP).doubleValue()
            - anotherTokenQuant;
        if (remainder / anotherTokenQuant > 0.0001) {
          throw new ContractValidateException("Not precise enough");
        }
      }
```

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeTransactionActuatorTest.java (L205-221)
```java
  @Test
  public void SameTokenNameCloseSuccessExchangeTransaction() {
    dbManager.getDynamicPropertiesStore().saveAllowSameTokenName(0);
    InitExchangeBeforeSameTokenNameActive();
    long exchangeId = 1;
    String tokenId = "_";
    long quant = 100_000_000L; // use 100 TRX to buy abc

    byte[] ownerAddress = ByteArray.fromHexString(OWNER_ADDRESS_SECOND);
    AccountCapsule accountCapsule = dbManager.getAccountStore().get(ownerAddress);
    Map<String, Long> assetMap = accountCapsule.getAssetMapForTest();
    Assert.assertEquals(20000_000000L, accountCapsule.getBalance());
    Assert.assertEquals(null, assetMap.get("def"));

    ExchangeTransactionActuator actuator = new ExchangeTransactionActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract(
        OWNER_ADDRESS_SECOND, exchangeId, tokenId, quant, 1));
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-231)
```java
    if (Arrays.equals(tokenID, firstTokenID)) {
      anotherTokenID = secondTokenID;
      anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
          .divide(bigFirstTokenBalance).longValueExact();
      newTokenBalance = addExact(firstTokenBalance, tokenQuant);
      newAnotherTokenBalance = addExact(secondTokenBalance, anotherTokenQuant);
    } else {
      anotherTokenID = firstTokenID;
      anotherTokenQuant = bigFirstTokenBalance.multiply(bigTokenQuant)
          .divide(bigSecondTokenBalance).longValueExact();
      newTokenBalance = addExact(secondTokenBalance, tokenQuant);
      newAnotherTokenBalance = addExact(firstTokenBalance, anotherTokenQuant);
    }

    if (anotherTokenQuant <= 0) {
      throw new ContractValidateException("the calculated token quant  must be greater than 0");
    }
```
