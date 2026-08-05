## Analysis Result

Java-tron's TRC10 `AssetIssueContract.precision` field is the direct analog of an ERC20/Chainlink "decimals" value — it declares how many decimal places a given asset uses [1](#0-0) . However, the TRX↔TRC10 bonding-curve `Exchange` (Bancor-style AMM) actuators never read or normalize by this field — they operate purely on the raw `firstTokenBalance`/`secondTokenBalance` integers, exactly the same class of bug as `DSCEngine` assuming a fixed 8-decimal scale for every Chainlink feed.

### Title
Exchange (Bancor-curve) pool actuators ignore per-asset `precision`, causing systematically mispriced swaps between TRC10 tokens with different decimals - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java])

### Summary
`ExchangeCreateActuator`, `ExchangeInjectActuator`, `ExchangeWithdrawActuator` and `ExchangeCapsule`/`ExchangeProcessor` implement a constant-supply bonding-curve exchange between two TRC10/TRX assets, but the pricing math treats `firstTokenBalance` and `secondTokenBalance` as directly comparable raw integer units regardless of each token's declared `precision`, mirroring the reported "assumes all USD feeds have 8 decimals" flaw where a fixed decimal assumption is baked into value/price computations that are actually decimal-dependent.

### Finding Description
`AssetIssueContract` declares an explicit `precision` field per TRC10 token, validated to be between 0 and `PRECISION_DECIMAL` (6) in `AssetIssueActuator` [2](#0-1)  and exposed via `AssetIssueCapsule.getPrecision()` [3](#0-2) . This is functionally equivalent to an ERC20 token's `decimals()`/a Chainlink feed's `decimals()` — it tells the protocol how the integer unit of an asset maps to its "real" economic value.

Despite this, the Exchange (Bancor curve) subsystem — which is reachable by any unprivileged account and directly moves user/pool funds — never consults `precision` anywhere:
- `ExchangeCreateActuator.execute`/`doValidate` pulls `firstTokenBalance`/`secondTokenBalance` straight from the contract and stores them as the pool's initial liquidity with no precision-based scaling [4](#0-3) .
- `ExchangeInjectActuator.execute` computes `anotherTokenQuant` via `floorDiv(multiplyExact(secondTokenBalance, tokenQuant), firstTokenBalance)` — a pure ratio of raw balances [5](#0-4) .
- `ExchangeWithdrawActuator.doValidate` performs the same raw-balance ratio math with `BigInteger`/`BigDecimal` for precision-loss checks, but again never scales by each token's declared `precision` [6](#0-5) .
- `ExchangeCapsule.transaction` and the underlying `ExchangeProcessor`/`SafeExchangeProcessor` Bancor-curve math (`exchangeToSupply`/`exchangeFromSupply`) operate purely on `firstTokenBalance`/`secondTokenBalance` as fungible integer units [7](#0-6) [8](#0-7) .

Because `precision` can differ between the two assets in a pool (0 up to 6, and TRX itself is fixed at 6 decimals/`1e6` sun-per-TRX), the bonding-curve math silently treats a "1 unit" difference on a precision-0 token as economically equal to a "1 unit" difference on a precision-6 token — an implicit, uniform-decimals assumption identical in nature to `DSCEngine` hardcoding `ADDITIONAL_FEED_PRECISION` for all price feeds.

### Impact Explanation
Any unprivileged account can create an `Exchange` pairing a TRC10 token with `precision=0` against one with `precision=6` (or against TRX, which is implicitly 6-decimal), seed it with an economically inconsistent nominal ratio, and then use `ExchangeInjectContract`/`ExchangeWithdrawContract`/`ExchangeTransactionContract` to trade against the mispriced curve. Because the AMM curve computes exchange amounts purely from raw integer balances, the effective price is off by up to `10^precisionDelta`, letting an attacker drain the counter-asset side of the pool (their own or another LP's) at a massively favorable rate — a direct fund-loss/settlement-mispricing impact, analogous to the health-factor/liquidation fund loss described in the original report.

### Likelihood Explanation
High for self-inflicted/attacker-created pools: exchange creation is permissionless (`ExchangeCreateActuator`), asset precision is user-chosen at issuance time (`AssetIssueActuator`), and trading against the pool requires no special privilege. The only barrier is that an attacker (or an unaware pool creator) must set up the mismatched-precision pool themselves, but nothing in validation logic prevents or warns about this, and nothing in the actuators normalizes for it.

### Recommendation
When creating an `Exchange` pool (`ExchangeCreateActuator`) and when computing swap/inject/withdraw amounts (`ExchangeInjectActuator`, `ExchangeWithdrawActuator`, `ExchangeCapsule.transaction`), fetch each token's `precision` via `AssetIssueCapsule.getPrecision()` (treating TRX as a fixed reference precision) and normalize balances/quantities to a common decimal base before applying the Bancor-curve math, restoring the raw integer amounts only for final storage/transfer.

### Proof of Concept
1. Issue TRC10 token `A` with `precision=0`, total supply `1_000_000`.
2. Issue TRC10 token `B` with `precision=6`, total supply `1_000_000`.
3. Call `ExchangeCreateContract` to create a pool with `firstTokenBalance(A)=1_000_000`, `secondTokenBalance(B)=1_000_000` — nominally "1:1" in raw units, but economically `A` unit is worth `10^6` times more real value than a `B` unit given their respective precisions.
4. Attacker calls `ExchangeTransactionContract`/`ExchangeInjectContract` selling small `A` amounts into the pool; because `ExchangeProcessor.exchange`/`ExchangeCapsule.transaction` compute the payout purely from raw balances (`chainbase/.../ExchangeCapsule.java:124-158`), the attacker receives `B` tokens at a rate that ignores the `10^6` precision disparity, extracting far more real economic value than deposited.
5. Repeat until the counter-asset side of the pool is drained, confirming loss of funds due to the uniform-decimals assumption baked into the Exchange math.

### Citations

**File:** protocol/src/main/protos/core/contract/asset_issue_contract.proto (L9-23)
```text
message AssetIssueContract {
  string id = 41;

  message FrozenSupply {
    int64 frozen_amount = 1; // asset amount
    int64 frozen_days = 2;
  }
  bytes owner_address = 1;
  bytes name = 2;
  bytes abbr = 3;
  int64 total_supply = 4;
  repeated FrozenSupply frozen_supply = 5;
  int32 trx_num = 6; // The fields trx_num and num define the exchange rate: num tokens can be purchased with trx_num TRX. This avoids using decimals.
  int32 precision = 7;
  int32 num = 8;
```

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L176-181)
```java
    int precision = assetIssueContract.getPrecision();
    if (precision != 0
        && dynamicStore.getAllowSameTokenName() != 0
        && (precision < 0 || precision > ActuatorConstant.PRECISION_DECIMAL)) {
      throw new ContractValidateException("precision cannot exceed 6");
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AssetIssueCapsule.java (L87-95)
```java
  public int getPrecision() {
    return this.assetIssueContract.getPrecision();
  }

  public void setPrecision(int precision) {
    this.assetIssueContract = this.assetIssueContract.toBuilder()
        .setPrecision(precision)
        .build();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L55-90)
```java
      byte[] firstTokenID = exchangeCreateContract.getFirstTokenId().toByteArray();
      byte[] secondTokenID = exchangeCreateContract.getSecondTokenId().toByteArray();
      long firstTokenBalance = exchangeCreateContract.getFirstTokenBalance();
      long secondTokenBalance = exchangeCreateContract.getSecondTokenBalance();

      long newBalance = subtractExact(accountCapsule.getBalance(), fee);

      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(firstTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, firstTokenBalance));
      } else {
        accountCapsule
            .reduceAssetAmountV2(firstTokenID, firstTokenBalance, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(secondTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, secondTokenBalance));
      } else {
        accountCapsule
            .reduceAssetAmountV2(secondTokenID, secondTokenBalance, dynamicStore, assetIssueStore);
      }

      long id = addExact(dynamicStore.getLatestExchangeNum(), 1);
      long now = dynamicStore.getLatestBlockHeaderTimestamp();
      if (dynamicStore.getAllowSameTokenName() == 0) {
        //save to old asset store
        ExchangeCapsule exchangeCapsule =
            new ExchangeCapsule(
                exchangeCreateContract.getOwnerAddress(),
                id,
                now,
                firstTokenID,
                secondTokenID
            );
        exchangeCapsule.setBalance(firstTokenBalance, secondTokenBalance);
```

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L214-243)
```java
    BigDecimal bigFirstTokenBalance = new BigDecimal(String.valueOf(firstTokenBalance));
    BigDecimal bigSecondTokenBalance = new BigDecimal(String.valueOf(secondTokenBalance));
    BigDecimal bigTokenQuant = new BigDecimal(String.valueOf(tokenQuant));
    final boolean allowHarden = allowHarden();
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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-158)
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
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L17-45)
```java
  private long exchangeToSupply(long balance, long quant) {
    logger.debug("balance: " + balance);
    long newBalance = balance + quant;
    logger.debug("balance + quant: " + newBalance);

    double issuedSupply = -supply * (1.0
        - Maths.pow(1.0 + (double) quant / newBalance, 0.0005, this.useStrictMath));
    logger.debug("issuedSupply: " + issuedSupply);
    long out = (long) issuedSupply;
    supply += out;

    return out;
  }

  private long exchangeFromSupply(long balance, long supplyQuant) {
    supply -= supplyQuant;

    double exchangeBalance = balance
        * (Maths.pow(1.0 + (double) supplyQuant / supply, 2000.0, this.useStrictMath) - 1.0);
    logger.debug("exchangeBalance: " + exchangeBalance);

    return (long) exchangeBalance;
  }

  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    long relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```
