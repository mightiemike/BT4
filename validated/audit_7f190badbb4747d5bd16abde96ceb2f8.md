### Title
Missing slippage protection (`expected`/`minOut`) in `ExchangeWithdrawContract` and `ExchangeInjectContract` allows value loss on TRX/TRC10 Bancor-style exchange pools - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
Unlike `ExchangeTransactionContract`, which carries an explicit `expected` field used to enforce a minimum-output slippage guard on trades, `ExchangeWithdrawContract` and `ExchangeInjectContract` carry no equivalent minimum/maximum bound. The counter-token amount (`anotherTokenQuant`) is computed purely from the on-chain pool ratio at the moment the transaction executes, with no user-supplied bound to protect against pool-ratio movement between transaction submission and block inclusion. This mirrors the Pendle `_onWithdraw` bug class: an operation that converts one asset into another via a pool has no caller-specified minimum-output/maximum-input parameter.

### Finding Description
`ExchangeTransactionContract` includes a protobuf field `expected` (minimum received tokens), enforced in `ExchangeTransactionActuator.doValidate()`: [1](#0-0) 

By contrast, `ExchangeWithdrawContract` has no such field in its protobuf definition: [2](#0-1) 

and `ExchangeWithdrawActuator.execute()` computes `anotherTokenQuant` strictly from the live pool balances at execution time, with no minimum/maximum bound supplied by the caller: [3](#0-2) 

The only checks performed in `doValidate()` are that quantities are positive and that the computed proportional amount is "precise enough" (a rounding-tolerance check, not a slippage/minOut guard): [4](#0-3) 

`ExchangeInjectActuator` (the liquidity-add analog) has the identical gap: `anotherTokenQuant` is derived from the current pool ratio with no user-specified bound, so a caller cannot cap how much of the second token they are willing to contribute: [5](#0-4) 

Because Tron blocks are produced by a single Super Representative each slot, and multiple `ExchangeTransactionContract` (trade) transactions can be included in the same block before a queued `ExchangeWithdrawContract`/`ExchangeInjectContract`, the pool ratio (`firstTokenBalance`/`secondTokenBalance`) used at execution time can differ substantially from the ratio at the time the withdraw/inject transaction was signed and broadcast. This is directly analogous to the reported Pendle bug: an exit/conversion step with `minOut` effectively fixed at "accept anything" rather than a caller-chosen floor.

### Impact Explanation
An exchange owner submitting `ExchangeWithdrawContract` to redeem their share of a TRC10/TRX Bancor-style pool can receive a counter-token amount far lower than expected if the pool ratio is manipulated (via ordinary trades, including by the block-producing SR reordering/inserting trades) before the withdraw executes. Symmetrically, `ExchangeInjectContract` callers can be forced to contribute more of the second token than anticipated. This causes direct value loss/asset accounting corruption for the account performing the withdraw/inject, with no on-chain mechanism to bound the loss.

### Likelihood Explanation
Exploitation only requires ordinary, unprivileged `ExchangeTransactionContract` trade transactions to shift the pool ratio between the time an `ExchangeWithdrawContract`/`ExchangeInjectContract` is broadcast and the time it is packed into a block — something any account can trigger, and something a block-producing SR can trivially engineer via transaction ordering within its own block. No special privilege, leaked key, or malicious peer behavior is required beyond normal transaction submission.

### Recommendation
Add an `expected`/bound field to `ExchangeWithdrawContract` (minimum acceptable `anotherTokenQuant`) and to `ExchangeInjectContract` (maximum acceptable `anotherTokenQuant`), and enforce it in `ExchangeWithdrawActuator.doValidate()`/`execute()` and `ExchangeInjectActuator.doValidate()`/`execute()`, mirroring the existing `expected` check already present in `ExchangeTransactionActuator`.

### Proof of Concept
1. Exchange owner broadcasts `ExchangeWithdrawContract` to withdraw `tokenQuant` of `firstTokenID`, expecting `anotherTokenQuant` computed from the current `firstTokenBalance`/`secondTokenBalance` ratio.
2. Before this transaction is included, other accounts (or the block-producing SR itself) submit `ExchangeTransactionContract` trades that shift the pool ratio unfavorably for the withdrawer.
3. When `ExchangeWithdrawActuator.execute()` runs, `anotherTokenQuant` is recomputed against the now-shifted balances at [6](#0-5) , and the withdrawer receives less counter-token than they would have received at broadcast time, with no `doValidate()` check able to reject the unfavorable execution since no minimum-output field exists.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** Tron protobuf protocol document.md (L1403-1420)
```markdown
     - message `ExchangeWithdrawContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to withdraw.
    
       `quant`: token amount to withdraw.
    
      ```java
      message ExchangeWithdrawContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
      }
      ```
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L74-89)
```java
      BigInteger bigFirstTokenBalance = new BigInteger(String.valueOf(firstTokenBalance));
      BigInteger bigSecondTokenBalance = new BigInteger(String.valueOf(secondTokenBalance));
      BigInteger bigTokenQuant = new BigInteger(String.valueOf(tokenQuant));
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
