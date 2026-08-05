### Title
Missing slippage protection in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` allows sandwich attacks against TRC10 bancor-pool liquidity operations - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
Java-tron implements a built-in bancor-formula AMM ("Exchange") for TRC10 token pairs, driven by four actuators: `ExchangeCreateActuator`, `ExchangeInjectActuator`, `ExchangeWithdrawActuator`, and `ExchangeTransactionActuator`. Unlike `ExchangeTransactionActuator`, which lets the caller supply an `expected` minimum-output bound for slippage protection, `ExchangeInjectContract` and `ExchangeWithdrawContract` have no such bound field, so the counter-token amount is always computed from the pool's spot balances at execution time with no way for the user to cap their exposure. This is the same root cause pattern as `OptionTokenV4.exerciseLP`: a fixed user-supplied quantity of one asset is matched against a *computed*, reserve-ratio–dependent amount of the other asset with no slippage check, making the operation exploitable via front-running/sandwiching.

### Finding Description
`ExchangeInjectActuator.execute` reads the current pool reserves (`firstTokenBalance`, `secondTokenBalance`) and derives `anotherTokenQuant` purely from the spot ratio at execution time: [1](#0-0) 

The corresponding `ExchangeInjectContract` protobuf message only carries `owner_address`, `exchange_id`, `token_id`, and `quant` — there is no `expected`/minimum bound field to constrain `anotherTokenQuant`: [2](#0-1) 

Contrast this with `ExchangeTransactionContract`, which does carry an `expected` field specifically for slippage protection on swaps: [3](#0-2) 

`doValidate()` in `ExchangeInjectActuator` only checks that the token exists in the exchange, that `tokenQuant > 0`, that resulting balances stay under `getExchangeBalanceLimit()`, and that the account balance is sufficient — it never checks that `anotherTokenQuant` falls within a range the caller intended: [4](#0-3) 

The same pattern exists in `ExchangeWithdrawActuator`, where `anotherTokenQuant` is likewise derived from spot balances with no bound, affecting how much of the paired token a withdrawer receives: [5](#0-4) 

Because `firstTokenBalance`/`secondTokenBalance` are read live inside the actuator (i.e., at block-packing/execution time, not at signing time), any transaction that changes the pool ratio before the victim's inject/withdraw transaction executes in the same block will change `anotherTokenQuant`. A block producer (or any user submitting a swap transaction ordered before the victim's) can execute an `ExchangeTransactionContract` swap immediately before the victim's `ExchangeInjectContract`/`ExchangeWithdrawContract`, exactly mirroring the sandwich pattern described in the report where `paymentAmountToAddLiquidity` is computed from manipulable spot reserves without any check bounding the deposited/received amount.

### Impact Explanation
A victim injecting liquidity can be forced to deposit a disproportionate amount of `anotherTokenID` relative to the pool state they observed when signing (skewed pricing), and a victim withdrawing liquidity can receive a disproportionately reduced amount of the paired token — both are direct, unprivileged, on-chain value losses caused purely by reserve-ratio manipulation around the victim's transaction, matching the "accounting/settlement" impact class (loss of value from an AMM-style operation lacking slippage protection).

### Likelihood Explanation
Any account can call `ExchangeTransactionContract` to move the pool ratio and any account can be the victim submitting `ExchangeInjectContract`/`ExchangeWithdrawContract` in the same block; transaction ordering within a block is controlled by the block producer/witness, and swap transactions are cheap TRC10 exchange calls, so this requires no special privilege — only the ability to observe pending transactions and order a swap before the victim's inject/withdraw call.

### Recommendation
Add an optional minimum/maximum bound parameter to `ExchangeInjectContract` and `ExchangeWithdrawContract` (analogous to `expected` in `ExchangeTransactionContract`), and validate the computed `anotherTokenQuant` against that bound in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` before mutating balances, failing the transaction if the pool has moved beyond the caller's tolerance.

### Proof of Concept
1. Attacker observes a pending `ExchangeInjectContract` from victim V (inject `X` of `tokenA` into exchange pool `(tokenA, tokenB)`).
2. Attacker submits `ExchangeTransactionContract` selling a large amount of `tokenA` into the same pool just before V's transaction is packed, which is processed via `ExchangeCapsule.transaction` and changes `firstTokenBalance`/`secondTokenBalance`. [6](#0-5) 
3. V's `ExchangeInjectActuator.execute` now computes `anotherTokenQuant` from the post-swap, skewed reserves rather than the reserves V observed when constructing the transaction, deducting a different (and for V, unfavorable) amount of `tokenB` from V's account. [7](#0-6) 
4. No validation step rejects this because neither `ExchangeInjectContract` nor `doValidate()` contains any check bounding `anotherTokenQuant`. [8](#0-7)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L60-83)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
      long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
      long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

      byte[] tokenID = exchangeInjectContract.getTokenId().toByteArray();
      long tokenQuant = exchangeInjectContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant;

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L196-256)
```java
    if (!Arrays.equals(tokenID, firstTokenID) && !Arrays.equals(tokenID, secondTokenID)) {
      throw new ContractValidateException("token id is not in exchange");
    }

    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }

    if (tokenQuant <= 0) {
      throw new ContractValidateException("injected token quant must greater than zero");
    }

    BigInteger bigFirstTokenBalance = new BigInteger(String.valueOf(firstTokenBalance));
    BigInteger bigSecondTokenBalance = new BigInteger(String.valueOf(secondTokenBalance));
    BigInteger bigTokenQuant = new BigInteger(String.valueOf(tokenQuant));
    long newTokenBalance;
    long newAnotherTokenBalance;

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

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (newTokenBalance > balanceLimit || newAnotherTokenBalance > balanceLimit) {
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

    if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(anotherTokenQuant, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(anotherTokenID, anotherTokenQuant, dynamicStore)) {
        throw new ContractValidateException("another token balance is not enough");
      }
    }
```

**File:** Tron protobuf protocol document.md (L1384-1401)
```markdown
     - message `ExchangeInjectContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to inject.
    
       `quant`: token amount to inject.
    
      ```java
      message ExchangeInjectContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
      }
      ```
```

**File:** Tron protobuf protocol document.md (L1422-1441)
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
