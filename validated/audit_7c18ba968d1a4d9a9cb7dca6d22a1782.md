## Analog Found

### Title
Unprivileged users can DOS `ExchangeTransactionActuator` swaps by pushing a token's pool balance to the `EXCHANGE_BALANCE_LIMIT` cap - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`)

### Summary
The Bancor-style exchange in java-tron enforces a global cap (`EXCHANGE_BALANCE_LIMIT`) on the token balances held inside an `Exchange` pool. Any unprivileged user can repeatedly perform legitimate swaps in one direction to push a token's pool balance close to this cap, which then causes every subsequent honest swap attempt in the same direction to revert. This is structurally analogous to the reported `TpdaLiquidationPair.swapExactAmountOut()` DOS, where an attacker exploits a bounded accounting limit (there the vault mint limit, here the exchange balance limit) to block other unprivileged participants from performing a legitimate state-changing operation.

### Finding Description
`ExchangeTransactionActuator.doValidate()` computes the new pool balance that would result from a swap and reverts if it exceeds the configured limit: [1](#0-0) 

`ExchangeInjectActuator` (creator-only) and `ExchangeCreateActuator` enforce the same cap when creating/injecting liquidity: [2](#0-1) [3](#0-2) 

The limit itself is a single global dynamic property, retrieved via: [4](#0-3) 

Critically, unlike `ExchangeInjectActuator` (which requires the caller to be the pool creator), `ExchangeTransactionActuator` is fully unprivileged — any account can call it to swap tokens through the pool, which directly increases `firstTokenBalance` or `secondTokenBalance` in the `ExchangeCapsule` via `exchangeCapsule.transaction(...)`: [5](#0-4) 

Because this balance is persistent on-chain state (not a per-transaction parameter), an attacker can perform a sequence of swaps that sell tokenA into the pool (increasing tokenA's pool balance) until it approaches `balanceLimit`. Once close to the cap, any further legitimate swap that would also increase tokenA's balance — i.e., any other user trying to sell tokenA into the same pool — will fail validation with `"token balance must less than " + balanceLimit"`, exactly mirroring the mechanism in the original report where `PrizeVault.liquidatableBalanceOf()`'s mint-limit check causes `TpdaLiquidationPair.swapExactAmountOut()` to revert once the cap is reached.

### Impact Explanation
This allows an unprivileged attacker to unilaterally deny other unprivileged users from trading in one direction on a given TRC10 exchange pool, by keeping the relevant side's balance pinned near `EXCHANGE_BALANCE_LIMIT`. This is a state-based, protocol-level DOS on a market/exchange primitive reachable by any account, matching the class of "underpriced/blocked public work" and "invalid-state" impact criteria: honest swap transactions revert due to an attacker-controlled global accounting limit, not due to insufficient liquidity or price movement. It does not require any privileged role (only `ExchangeTransactionActuator` calls are needed, which are open to everyone), unlike `ExchangeInjectActuator` which is creator-restricted.

### Likelihood Explanation
Exploitation requires the attacker to hold and repeatedly swap a large amount of the token in question to approach the fixed `EXCHANGE_BALANCE_LIMIT` (analogous to needing large capital in the original report, since `EXCHANGE_BALANCE_LIMIT` is typically set to a large fixed value, e.g. `1_000_000_000_000_000` as seen in test fixtures). This bounds the practicality to well-capitalized attackers or low-value/high-supply tokens, similar to the original medium-severity finding, but the mechanism is fully reachable with normal, unprivileged transactions and no special conditions.

### Recommendation
Consider decoupling the balance-limit check from blocking legitimate swaps outright — e.g., allow swaps that reduce a token's pool balance even when the opposite side is capped, or make the cap apply only to liquidity injection (`ExchangeInjectActuator`/`ExchangeCreateActuator`) rather than to ordinary `ExchangeTransactionActuator` swaps, since organic trading activity should not be able to indefinitely block itself via a static global cap.

### Proof of Concept
1. An attacker repeatedly calls `ExchangeTransactionActuator` selling `tokenA` for `tokenB` in a given `Exchange`, each time increasing `firstTokenBalance` (or `secondTokenBalance`) via `ExchangeCapsule.transaction()`. [6](#0-5) 
2. Once the balance approaches `dynamicStore.getExchangeBalanceLimit()`, any other user's attempt to sell `tokenA` into the same pool fails validation: [1](#0-0) 
3. Legitimate traders wishing to sell `tokenA` are denied service until the attacker (or others) reduce the pool's `tokenA` balance, at the attacker's discretion.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L199-205)
```java
    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance
        : secondTokenBalance);
    tokenBalance = addExact(tokenBalance, tokenQuant);
    if (tokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L233-236)
```java
    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (newTokenBalance > balanceLimit || newAnotherTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L205-208)
```java
    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (firstTokenBalance > balanceLimit || secondTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L1636-1642)
```java
  public long getExchangeBalanceLimit() {
    return Optional.ofNullable(getUnchecked(EXCHANGE_BALANCE_LIMIT))
        .map(BytesCapsule::getData)
        .map(ByteArray::toLong)
        .orElseThrow(
            () -> new IllegalArgumentException("not found EXCHANGE_BALANCE_LIMIT"));
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
