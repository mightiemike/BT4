### Title
Missing Slippage/Minimum-Output Protection in `ExchangeInjectActuator` Enables Sandwich Attacks on Liquidity Providers - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
`ExchangeInjectContract`, the java-tron analog of a DEX "add liquidity" operation, carries only `token_id` and `quant` — there is no counterpart to `amountAMin`/`amountBMin` from the Pancake Router bug report. The paired-token amount (`anotherTokenQuant`) is computed from the exchange pool's *current* on-chain reserves at execution time with no upper bound the caller can enforce, so any account can manipulate the pool ratio immediately before/after the liquidity-adding transaction is packed into a block and extract value from the injector, exactly the "0 slippage protection" bug class described in the external report.

### Finding Description
`ExchangeInjectContract` only contains `owner_address`, `exchange_id`, `token_id`, `quant` [1](#0-0) . Compare this with `ExchangeTransactionContract`, which does include an `expected` (minimum) field used to bound slippage [2](#0-1)  and is enforced in `ExchangeTransactionActuator.doValidate()` via `if (anotherTokenQuant < tokenExpected) throw ...` [3](#0-2) .

`ExchangeInjectActuator`, however, computes the paired amount purely from the pool's live reserves with no min/max bound supplied by the caller: [4](#0-3) 

The only checks performed are that `anotherTokenQuant > 0`, it doesn't exceed `balanceLimit`, and the account has sufficient balance [5](#0-4)  — there is no check that `anotherTokenQuant` stays within a range the submitter actually agreed to when signing the transaction. Because the exchange reserves (`firstTokenBalance`/`secondTokenBalance`) can be changed by *any other account* submitting an `ExchangeTransactionContract` trade against the same pool in the same or an earlier block, an attacker can shift the ratio right before the victim's `ExchangeInjectContract` executes, forcing the victim to inject a far more (or less) favorable amount of the paired token than intended, then reverse the trade to capture the difference — a classic sandwich attack. Both `ExchangeTransactionActuator.calcFee()` and `ExchangeInjectActuator.calcFee()` return `0` [6](#0-5) , so there is no fee friction discouraging the attack.

### Impact Explanation
This is a concrete asset/accounting corruption vector reachable purely via broadcast transactions from unprivileged accounts (the "creator" restriction on `ExchangeInjectContract` is not an administrative privilege — any account can become a pool "creator" by calling the permissionless `ExchangeCreateActuator`, then later inject liquidity, exposing itself to this attack). An attacker who observes a pending `ExchangeInjectContract` in the mempool can extract value from the liquidity provider by manipulating the pool state around the victim's transaction, with no on-chain fee cost to the exchange actuators discouraging repeated exploitation.

### Likelihood Explanation
Likelihood is elevated because: (1) pending transactions are visible in the P2P mempool before block inclusion, (2) block producers or any node monitoring the mempool can order/insert their own `ExchangeTransactionContract` trades around the victim's `ExchangeInjectContract` within the same block or across adjacent blocks, and (3) the exchange actuators charge zero fee, making repeated sandwich attempts essentially free.

### Recommendation
Add a caller-specified bound to `ExchangeInjectContract` (e.g., `min_another_quant` / `max_another_quant`, analogous to `expected` in `ExchangeTransactionContract`) and enforce it in `ExchangeInjectActuator.doValidate()`/`execute()` before committing the balance changes, so the liquidity injector can guarantee the paired-token amount stays within an accepted range regardless of intervening pool-ratio manipulation.

### Proof of Concept
1. Victim broadcasts `ExchangeInjectContract{exchange_id=X, token_id=TRX, quant=Q}` intending to add TRX liquidity to pool X at the currently observed ratio.
2. Attacker observes this pending transaction and broadcasts an `ExchangeTransactionContract` selling a large amount of the paired token into pool X, shifting `firstTokenBalance`/`secondTokenBalance` (using the code path at `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java:64-69`), executed in the same block or immediately prior to the victim's transaction (zero fee cost per `calcFee()`).
3. The victim's `ExchangeInjectContract` then executes against the now-skewed reserves; `anotherTokenQuant` in `ExchangeInjectActuator.java:215-227` is computed from the manipulated ratio, forcing the victim to inject far more of the paired token than intended, with no `expected`/minimum field to reject the unfavorable execution.
4. Attacker submits a reversing `ExchangeTransactionContract` to restore the original ratio, capturing the surplus paired-token value extracted from the victim's injected liquidity, all at zero actuator fee.

### Citations

**File:** Tron protobuf protocol document.md (L1394-1401)
```markdown
      ```java
      message ExchangeInjectContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
      }
      ```
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-227)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L229-256)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L267-270)
```java
  @Override
  public long calcFee() {
    return 0;
  }
```
