None of the `Exchange*Actuator` validators (`ExchangeCreateActuator`, `ExchangeInjectActuator`, `ExchangeWithdrawActuator`, `ExchangeTransactionActuator`) reference `AssetIssueContract.precision` anywhere. This confirms the bug-class analog exists.

### Title
Bancor-style TRC10 Exchange pool math ignores per-token `precision`, causing mispriced swaps/injects/withdrawals across tokens with different decimals - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java, ExchangeInjectActuator.java, ExchangeWithdrawActuator.java, ExchangeTransactionActuator.java)

### Summary
The TRC10 `AssetIssueContract` allows each token to declare its own `precision` (0-6) field independent of TRX's fixed 6-decimal ("sun") unit. However, the entire Exchange (Bancor-relay) subsystem — `ExchangeCreateActuator`, `ExchangeInjectActuator`, `ExchangeWithdrawActuator`, and `ExchangeTransactionActuator` — operates purely on raw `long` "quant" values (`firstTokenBalance`/`secondTokenBalance`/`tokenQuant`) with no normalization by each token's `precision`. This mirrors the OmoOracle bug class: a value-conversion routine that assumes all assets share the same decimal scale (there, hardcoded 1e18; here, implicitly "1 unit = 1 unit") when in fact assets can have different decimals.

### Finding Description
`AssetIssueContract` defines a `precision` field (`protocol/src/main/protos/core/contract/asset_issue_contract.proto:22`), validated in `AssetIssueActuator` to be between 0 and `ActuatorConstant.PRECISION_DECIMAL` (6) [1](#0-0) . This means two TRC10 tokens can legitimately have different decimal precisions (e.g., one with precision 0, one with precision 6), so "1 unit" of one token is not economically comparable to "1 unit" of another.

The Exchange actuators, however, treat `firstTokenBalance`/`secondTokenBalance`/`tokenQuant` as directly fungible raw integers with no precision normalization:
- `ExchangeCreateActuator.doValidate()`/`execute()` pulls `firstTokenBalance`/`secondTokenBalance` straight from the contract and stores them into the `ExchangeCapsule` pool with no precision check or scaling [2](#0-1) .
- The core AMM math in `ExchangeCapsule.transaction()` and `ExchangeProcessor`/`SafeExchangeProcessor` operates purely on these raw `long` balances via a constant-product/Bancor-relay formula, with no decimals adjustment [3](#0-2) [4](#0-3) .
- `ExchangeInjectActuator` and `ExchangeWithdrawActuator` compute `anotherTokenQuant` via simple ratio of raw balances (`secondTokenBalance * tokenQuant / firstTokenBalance`), again with no precision scaling [5](#0-4) [6](#0-5) .
- `ExchangeTransactionActuator` executes swaps the same way, converting `tokenQuant` into `anotherTokenQuant` via `exchangeCapsule.transaction()` without any decimals conversion [7](#0-6) .
- Nowhere in `AbstractExchangeActuator` (the shared base class for all four actuators) is `AssetIssueContract.getPrecision()` referenced or used to scale amounts [8](#0-7) .

Because the exchange price is fully determined by the ratio of raw integer balances (`firstTokenBalance : secondTokenBalance`), a pool created between a token with precision 0 and a token with precision 6 (or any two tokens with unequal precision) will price 1 raw unit of each token as equal, which — depending on which side has finer precision — is off by a factor of up to `10^6`. Any owner-controlled `ExchangeCreateContract` can set up such a mismatched pool, and any account can then call `ExchangeTransactionContract` to swap against the pool at the distorted price.

### Impact Explanation
An attacker (or naive user) creating an exchange between two TRC10 tokens with different `precision` values establishes a pool whose implied price is wrong by an order of magnitude related to `10^(precisionA - precisionB)`. Because the pool balances are the sole source of pricing in `ExchangeProcessor`/`SafeExchangeProcessor`, subsequent `ExchangeTransactionContract` swaps executed by other users (or by the attacker themselves) will transfer real token value at the distorted ratio, allowing extraction of value from counterparties who assume the pool prices tokens at their nominal (decimals-adjusted) value. This directly causes asset/accounting corruption within the on-chain TRC10 exchange mechanism, reachable purely from broadcast transactions (`ExchangeCreateContract`, `ExchangeInjectContract`, `ExchangeTransactionContract`, `ExchangeWithdrawContract`) with no privileged role required.

### Likelihood Explanation
Likelihood is Medium: exploitation requires (1) the existence or creation of TRC10 tokens with differing non-zero `precision` values, and (2) an exchange pool being created/used between such tokens. This is fully within the reach of any account issuing an `AssetIssueContract` with `precision` up to 6 and any account able to submit `ExchangeCreateContract`/`ExchangeTransactionContract` — no special privileges are needed — but it does depend on some tokens actually adopting non-zero precision and someone creating/trading such a pool.

### Recommendation
Normalize all Exchange balances and quantities by each token's declared `precision` before performing pool math (mirroring the OmoOracle fix: `amountUSD = amountToken * price * decimalsUSD / (decimalsToken * 1e18)`). Concretely: when creating/injecting/withdrawing/swapping in `ExchangeCreateActuator`, `ExchangeInjectActuator`, `ExchangeWithdrawActuator`, and `ExchangeTransactionActuator`, look up each token's `AssetIssueContract.getPrecision()` and scale `firstTokenBalance`, `secondTokenBalance`, and `tokenQuant` to a common decimal base (e.g., 6, matching TRX) before invoking `ExchangeCapsule.transaction()`/`ExchangeProcessor`, and de-scale results before crediting accounts. Alternatively, restrict Exchange creation to only allow pooling between tokens with identical `precision`.

### Proof of Concept
1. Issue token A with `precision = 0` and token B with `precision = 6` via `AssetIssueContract` (validated only for range 0-6, not for exchange compatibility) [1](#0-0) .
2. Attacker calls `ExchangeCreateContract` with `firstTokenBalance = 1_000_000` (token A, precision 0 → nominally 1,000,000 whole tokens) and `secondTokenBalance = 1_000_000` (token B, precision 6 → nominally 1 whole token), creating a pool that treats these as equal value [9](#0-8) .
3. A victim, assuming standard decimal-adjusted pricing, calls `ExchangeTransactionContract` to swap token B for token A; `ExchangeCapsule.transaction()` computes the output purely from raw balances, giving the attacker's counter-token at a rate that undervalues token B by 10^6 [3](#0-2) .
4. The attacker withdraws/injects further to extract the mispriced value, with `ExchangeWithdrawActuator`/`ExchangeInjectActuator` performing the same unscaled ratio math, compounding the loss for the counterparty [6](#0-5) .

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L176-181)
```java
    int precision = assetIssueContract.getPrecision();
    if (precision != 0
        && dynamicStore.getAllowSameTokenName() != 0
        && (precision < 0 || precision > ActuatorConstant.PRECISION_DECIMAL)) {
      throw new ContractValidateException("precision cannot exceed 6");
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-69)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());
```

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L1-24)
```java
package org.tron.core.actuator;

import com.google.protobuf.GeneratedMessageV3;
import org.tron.common.math.StrictMathWrapper;
import org.tron.protos.Protocol.Transaction.Contract.ContractType;

public abstract class AbstractExchangeActuator extends AbstractActuator {

  public AbstractExchangeActuator(ContractType type, Class<? extends GeneratedMessageV3> clazz) {
    super(type, clazz);
  }

  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }

  public long subtractExact(long x, long y) {
    return allowHarden() ? StrictMathWrapper.subtractExact(x, y) : x - y;
  }

  public long addExact(long x, long y) {
    return allowHarden() ? StrictMathWrapper.addExact(x, y) : x + y;
  }
}
```
