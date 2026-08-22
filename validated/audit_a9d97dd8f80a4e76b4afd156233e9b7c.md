Based on my research, I've confirmed that java-tron's on-chain Exchange module (a Bancor-relay-style bonding-curve AMM) is the strongest reachable analog to the front-run/sandwich bug class described in the report.

### Title
Sandwich/front-run of `ExchangeTransactionContract` trades lets an attacker extract value from the bonding-curve `Exchange` pool - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`)

### Summary
`ExchangeTransactionActuator`, reachable by any account via a broadcast `ExchangeTransactionContract`, executes trades against a Bancor-relay bonding curve whose price is derived purely from the exchange's current `firstTokenBalance`/`secondTokenBalance` at execution time, with no time-weighting, oracle, or block-level anti-manipulation check. This is architecturally the same bug class as the reported `LiquidityPool` issue: reward/price is a function of instantaneous pool state that any address can shift immediately before another user's transaction executes and shift back immediately after, extracting value at the victim's expense.

### Finding Description
`ExchangeTransactionActuator.execute()` calls `ExchangeCapsule.transaction()` [1](#0-0) , which computes the swap output purely from the current `firstTokenBalance`/`secondTokenBalance` of the exchange pool via the Bancor-relay formula in `ExchangeProcessor`/`SafeExchangeProcessor` [2](#0-1) [3](#0-2) . There is no mechanism recording or enforcing a time-weighted price, and any account can submit an `ExchangeTransactionContract` to shift the pool balances at will, immediately before and after a victim's pending transaction (front-run using higher energy/fee ordering, then back-run in the same or next block), since block producers order transactions by fee/arrival and there is no commit-reveal or per-block price lock. The only protection given to the victim is the `expected` (minimum output) field checked in `doValidate()` [4](#0-3) , which only guarantees a floor and does not prevent the attacker from capturing the price-impact spread through a classic sandwich (buy before victim to raise price, sell after to profit from the price the victim pushed further).

### Impact Explanation
An attacker can systematically extract value from every trade against a TRC10/TRX `Exchange` pool by sandwiching victim transactions, corrupting the fairness of the on-chain AMM's asset accounting and directly transferring economic value from ordinary users to the attacker, without needing any privileged role, leaked key, or malicious peer status — only the ability to submit ordinary transactions with competitive fees.

### Likelihood Explanation
The attack is trivially and continuously exploitable: `ExchangeTransactionContract` is a standard, permissionless, publicly documented contract type [5](#0-4) , exchange pool balances are public on-chain state, and the pricing math itself has no anti-sandwich defenses; this mirrors mempool-observation sandwich techniques identical to the referenced report and requires no special access.

### Recommendation
Introduce time-weighted or oracle-based pricing checks for `Exchange` trades (e.g., limit maximum price movement per block, or use a TWAP over multiple blocks), and/or add a minimum time/block delay between successive trades touching the same pool from different accounts within a sandwich window; alternatively, bound the maximum single-trade price impact and require multi-block settlement for large trades to remove the incentive for front/back-running around a single victim transaction.

### Proof of Concept
1. Attacker monitors the transaction pool/mempool for a pending `ExchangeTransactionContract` from a victim trading `tokenA` for `tokenB` in exchange `X`.
2. Attacker submits `ExchangeTransactionContract` A→B with a higher energy fee to front-run, shifting `firstTokenBalance`/`secondTokenBalance` away from equilibrium via `ExchangeCapsule.transaction()` [6](#0-5) .
3. Victim's trade executes at the now-worse price (still passing because `expected` only sets a floor, not a ceiling on attacker-induced slippage) [7](#0-6) .
4. Attacker submits a second `ExchangeTransactionContract` B→A immediately after, capturing the price impact the victim's trade created, restoring the pool near equilibrium and pocketing the spread.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L64-69)
```java
      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L186-221)
```java
    if (tokenQuant <= 0) {
      throw new ContractValidateException("token quant must greater than zero");
    }

    if (tokenExpected <= 0) {
      throw new ContractValidateException("token expected must greater than zero");
    }

    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance
        : secondTokenBalance);
    tokenBalance = addExact(tokenBalance, tokenQuant);
    if (tokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }

    if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(tokenQuant, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(tokenID, tokenQuant, dynamicStore)) {
        throw new ContractValidateException("token balance is not enough");
      }
    }

    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L41-45)
```java
  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    long relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-169)
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

    if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
      throw new ContractValidateException("Exchange balance must be >=0 after transaction");
    }
    this.exchange = this.exchange.toBuilder()
        .setFirstTokenBalance(newFirstTokenBalance)
        .setSecondTokenBalance(newSecondTokenBalance)
        .build();

    return buyTokenQuant;
  }
```

**File:** Tron protobuf protocol document.md (L1422-1442)
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
      }
      ```
```
