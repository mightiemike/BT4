### Title
Missing minimum-reserve floor in TRON's TRC10 `Exchange` AMM lets a pool be drained to near-zero reserves, causing unprivileged traders to lose deposited tokens for near-zero output — ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java])

### Summary
java-tron's TRC10 `Exchange` feature (`ExchangeCreateActuator`, `ExchangeInjectActuator`, `ExchangeWithdrawActuator`, `ExchangeTransactionActuator`) implements a Bancor-relay-style constant-supply AMM where `firstTokenBalance`/`secondTokenBalance` play the role of the Spartan `Pool`'s reserves. Unlike the Spartan finding, LP share minting/burning is not directly exposed to arbitrary users — `Inject`/`Withdraw` are restricted to the pool's `creator` [1](#0-0) . However, exactly like the reported class of bug, there is **no minimum-reserve floor**: any account can create a pool with reserves as low as `1` [2](#0-1) , and the creator (privileged only w.r.t. their own, self-created pool — the same privilege level as the Spartan PoC attacker who created and then burned their own `Pool`) can repeatedly call `ExchangeWithdrawActuator` to shrink reserves down to near `1/1`, since the only floor check is `firstTokenBalance == 0 || secondTokenBalance == 0` [3](#0-2) . Once reserves are crippled, any **unprivileged** account calling `ExchangeTransactionActuator` (open to anyone, no ownership check) against that pool can have real tokens/TRX deducted from their account while receiving an output rounded down to 0 or a negligible amount from the bonding-curve math in `ExchangeProcessor`/`SafeExchangeProcessor`, mirroring the "deposit 1,000,000 DAI, receive 0 LP" PoC from the report.

### Finding Description
The AMM math lives in `ExchangeProcessor.exchange`/`SafeExchangeProcessor.exchange`, which compute output via a Bancor relay-supply formula that is highly sensitive to reserve size [4](#0-3) . `ExchangeCapsule.transaction` mutates the live reserves for every unprivileged `ExchangeTransactionActuator` call [5](#0-4) .

The pool-reserve equivalent of the Spartan `totalSupply` can be driven arbitrarily low because:
1. `ExchangeCreateActuator.doValidate()` only requires `firstTokenBalance > 0 && secondTokenBalance > 0`, with no minimum floor [2](#0-1) .
2. `ExchangeWithdrawActuator.doValidate()` similarly only rejects a withdraw that would take a balance to exactly `0` [3](#0-2) , and permits progressively withdrawing down to `1` unit on each side across multiple transactions.
3. `ExchangeTransactionActuator`, which is callable by **any account**, only checks that current reserves are non-zero before computing a swap [6](#0-5) ; it does not validate that reserves are large enough to give a sane quote.
4. The `tokenExpected` slippage check exists only in `doValidate()` [7](#0-6)  — a caller who (naively, or via a poorly-configured integration/wallet) submits `expected=0` bypasses any meaningful protection, and `execute()` unconditionally deducts the input token/TRX and credits whatever (possibly 0 or near-0) `anotherTokenQuant` the degenerate reserves produce [8](#0-7) .

This is structurally the same bug class as the Spartan report: an actor privileged only over their own self-created pool manipulates the pool's internal "supply"/reserve accounting to a degenerate value, after which any *other, unprivileged* participant interacting with that pool via a public, permissionless entry point can be induced into a transaction that debits real value for near-zero (or badly mispriced) return.

### Impact Explanation
Any account can create a "trap" `Exchange` (e.g. TRX↔some TRC10) with minimal reserves, or drain a legitimately-created pool it owns down to near-1 reserves via repeated withdrawals, then advertise/route trades through it. Victims who transact against it via the fully public `ExchangeTransactionActuator` lose their input asset (TRX or TRC10, deducted unconditionally in `execute()`) while receiving output that can round to zero or be economically negligible relative to what they put in, when their client does not set a tight `expected` value. This is a direct on-chain fund-loss / accounting-integrity issue reachable by any unprivileged user, matching the "underpriced work" / "accounting divergence" impact class.

### Likelihood Explanation
- Pool creation is fully permissionless (`ExchangeCreateActuator`) and cheap (a fixed fee) [9](#0-8) .
- Draining down to minimal (`1`) reserves via `ExchangeWithdrawActuator` requires only being the pool's own creator, which is trivially satisfiable by the attacker on their own pool.
- The victim-facing entry point (`ExchangeTransactionActuator`) has no built-in minimum-reserve or minimum-output sanity check independent of the caller-supplied `expected` parameter, so any wallet/bot/contract integration that queries a quote and submits with loose slippage tolerance is exposed.

### Recommendation
- Enforce a minimum reserve floor (e.g., require `firstTokenBalance` and `secondTokenBalance` to remain above a protocol-defined minimum, not just `> 0`) in both `ExchangeCreateActuator.doValidate()` and `ExchangeWithdrawActuator.doValidate()`.
- Consider rejecting/flagging `ExchangeTransactionActuator` calls where computed `anotherTokenQuant` is disproportionately small relative to reserves, independent of caller-supplied `expected`.
- Alternatively/additionally, require withdrawals that would take either reserve below a safe threshold to be rejected outright, closing the same class of "degenerate pool" attack the Spartan report describes for LP-token burning.

### Proof of Concept
1. Attacker calls `ExchangeCreateActuator` to create Exchange `E` with `firstTokenBalance = 2`, `secondTokenBalance = 2` (passes the `> 0` check in [2](#0-1) ).
2. Attacker repeatedly calls `ExchangeWithdrawActuator` (they are the creator, so `validate()` passes the creator check [1](#0-0) ) until reserves are minimal, e.g. `firstTokenBalance = 1`.
3. A victim, unaware of the degenerate reserves, calls `ExchangeTransactionActuator` to swap a large amount of TRX/TRC10 into `E`, either with `expected = 0` or via an integration that doesn't independently sanity-check the quote.
4. `ExchangeCapsule.transaction()` computes `anotherTokenQuant` via the Bancor relay formula against the crippled reserves [10](#0-9) , returning `0` or a negligible amount.
5. `ExchangeTransactionActuator.execute()` unconditionally deducts the victim's input token/TRX and credits the negligible `anotherTokenQuant` [11](#0-10) , resulting in real fund loss for the victim — directly analogous to the reported PoC where depositing 1,000,000 DAI into a `totalSupply = 1` pool returned 0 LP tokens.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L181-183)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L209-212)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L36-34)
```java

```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L201-203)
```java
    if (firstTokenBalance <= 0 || secondTokenBalance <= 0) {
      throw new ContractValidateException("token balance must greater than zero");
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L77-98)
```java
      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());
      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, tokenQuant));
      } else {
        accountCapsule.reduceAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .addAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }

      accountStore.put(accountCapsule.createDbKey(), accountCapsule);

      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);

      ret.setExchangeReceivedAmount(anotherTokenQuant);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L194-197)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
