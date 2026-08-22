Confirmed: `ExchangeInjectContract` and `ExchangeWithdrawContract` (unlike `ExchangeTransactionContract`) expose no `expected`/min-out field at all, so the AMM-computed `anotherTokenQuant` is accepted unconditionally at execution time.

### Title
Missing slippage protection in ExchangeInject/ExchangeWithdraw actuators allows front-running of TRC10 AMM liquidity operations - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
The `ExchangeInjectContract` and `ExchangeWithdrawContract` messages (java-tron's Bancor-style TRC10 AMM pool) carry only `owner_address`, `exchange_id`, `token_id`, and `quant` — there is no user-controllable "minimum expected" counter-token amount, unlike `ExchangeTransactionContract`, which does carry an `expected` field that is enforced in `ExchangeTransactionActuator.doValidate` [1](#0-0) . Because inject/withdraw compute the paired-token amount from the pool's live balances at execution time with no bound supplied by the caller, this is the same class of bug as the reported Particle finding: hardcoded/absent slippage limits allow a much worse ratio to be applied than the one the caller observed when they built their transaction.

### Finding Description
`ExchangeInjectContract`/`ExchangeWithdrawContract` are defined without any `expected`/min-out parameter [2](#0-1) .

In `ExchangeInjectActuator.execute`, `anotherTokenQuant` is derived purely from the exchange's current on-chain `firstTokenBalance`/`secondTokenBalance` ratio and immediately used to debit the account and update pool balances, with no comparison against any user-supplied bound: [3](#0-2) 

Likewise, `ExchangeWithdrawActuator.execute` computes `anotherTokenQuant` from the live pool ratio and credits it directly, again with no caller-specified minimum: [4](#0-3) 

`doValidate` for both actuators only checks that the caller is the exchange creator, that quantities are positive, and (for withdraw) a rounding/precision tolerance — none of these protect against the pool ratio having moved between transaction construction and block inclusion: [5](#0-4) 

Any account can move the pool price by broadcasting `ExchangeTransactionContract` trades against the same `exchange_id` immediately before an inject/withdraw is packed, exactly like a Uniswap-style frontrun of `mint`/`increaseLiquidity`/`decreaseLiquidity` with `amountMin = 0`.

### Impact Explanation
An attacker (any account able to broadcast an `ExchangeTransactionContract`) can sandwich or simply race a victim's `ExchangeInjectContract`/`ExchangeWithdrawContract` transaction: trade against the pool to skew its ratio, let the victim's inject execute at the skewed ratio (causing the victim to receive a disadvantageous exchange-share allocation or, on withdraw, to receive fewer of the paired token than the ratio at submission time implied), then trade back. This is direct value extraction from a legitimate on-chain accounting operation (asset/accounting corruption for the exchange creator), reachable purely via public broadcast transactions — no privileged role required.

### Likelihood Explanation
Likelihood is high: TRC10 exchanges are a long-standing, publicly usable java-tron feature; any address can submit `ExchangeTransactionContract` transactions against any `exchange_id`, and transaction ordering within a block/production window is influenced by fee/energy and mempool propagation, giving an attacker a practical window to front-run a pending inject/withdraw without needing consensus-level privilege.

### Recommendation
Add a caller-supplied minimum-expected-amount field to `ExchangeInjectContract` and `ExchangeWithdrawContract` (analogous to `expected` in `ExchangeTransactionContract`), and enforce it in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` `doValidate`/`execute` by rejecting the operation if the computed `anotherTokenQuant` is worse than the caller's specified bound, mirroring the existing check at `ExchangeTransactionActuator.java:219-221`.

### Proof of Concept
1. Attacker observes a pending `ExchangeInjectContract` (or `ExchangeWithdrawContract`) from victim V against `exchange_id = X` with `token_id = T`, `quant = Q`.
2. Attacker broadcasts an `ExchangeTransactionContract` trading a large amount into/out of pool `X` to shift `firstTokenBalance`/`secondTokenBalance`, and ensures it lands in the block before V's transaction (higher fee/energy priority or timing).
3. V's inject/withdraw transaction is validated and executed against the now-skewed ratio in `ExchangeInjectActuator.execute`/`ExchangeWithdrawActuator.execute`, since neither method checks the resulting `anotherTokenQuant` against any minimum V specified — because no such field exists in the contract.
4. V receives (on withdraw) a smaller `anotherTokenQuant` of the paired token, or (on inject) contributes at an unfavorable ratio, than the ratio observed when V built the transaction; attacker optionally reverses their trade to restore the pool and capture the difference.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** Tron protobuf protocol document.md (L1384-1420)
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L71-92)
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

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());
      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, tokenQuant));
      } else {
        accountCapsule.reduceAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L77-104)
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

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, tokenQuant));
      } else {
        accountCapsule.addAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .addAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
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
