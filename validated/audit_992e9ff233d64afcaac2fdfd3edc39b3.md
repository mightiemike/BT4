## Analysis Result

### Title
Missing slippage/minimum-output protection in `ExchangeInjectActuator` and `ExchangeWithdrawActuator` enables sandwich attacks on TRON's Bancor-style TRC10 exchange - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
Java-tron's built-in AMM/liquidity-pool feature (`ExchangeInjectContract`/`ExchangeWithdrawContract`) computes the counter-token amount from the *current* pool ratio at execution time, but the contract messages expose no user-supplied minimum/maximum bound analogous to Vader's `amountAMin`/`amountBMin`. Unlike `ExchangeTransactionContract`, which carries and enforces an `expected` field, the inject/withdraw contracts have no slippage-limiting field at all, so a broadcast transaction can be sandwiched by any node/validator that observes it in the mempool before block inclusion.

### Finding Description
`ExchangeInjectContract` and `ExchangeWithdrawContract` only carry `owner_address`, `exchange_id`, `token_id`, and `quant` — no expected/minimum counter-amount field: [1](#0-0) 

In `ExchangeInjectActuator.execute`, the counter-token quantity (`anotherTokenQuant`) is derived purely from the live pool balances at the moment of execution and immediately debited from the caller's account, with no comparison to any caller-supplied bound: [2](#0-1) 

Likewise, `ExchangeWithdrawActuator.execute` computes `anotherTokenQuant` from the current pool ratio with no minimum-received guard from the caller: [3](#0-2) 

`doValidate()` for withdraw only checks internal precision/rounding tolerance (`"Not precise enough"`), not a caller-specified slippage bound: [4](#0-3) 

By contrast, `ExchangeTransactionContract` *does* carry an `expected` field and `ExchangeTransactionActuator.doValidate()` explicitly enforces it (`anotherTokenQuant < tokenExpected` -> reject), which is exactly the "used slippage param" pattern the original report says is missing on the AMM's liquidity side: [5](#0-4) 

This confirms the design gap is specific to the liquidity provision/removal path (`Inject`/`Withdraw`), the direct analog of Vader's `addLiquidity`, while the swap path (`Transaction`) already has protection.

### Impact Explanation
Because a submitted `ExchangeInjectContract`/`ExchangeWithdrawContract` transaction is visible in the network mempool before block confirmation, an attacker (any node relaying transactions, or a witness producing the block) can observe a pending inject/withdraw and front-run it with their own trade against the same exchange pair to shift the pool ratio, then let the victim's transaction execute at the manipulated ratio, and finally back-run to restore/extract the difference. For `ExchangeInjectActuator`, this can force the victim to contribute a disproportionately large amount of the second asset for the same first-asset `quant`; for `ExchangeWithdrawActuator`, it can force the victim to receive less of the counter-asset than the fair-ratio amount. Both cases result in a direct, unrecoverable value transfer from the victim to the attacker — an accounting/asset corruption reachable purely via broadcasting normal, unprivileged transactions.

### Likelihood Explanation
Any account holding TRX/TRC10 balance in the affected exchange pair can trigger this by simply submitting a normal `ExchangeInjectContract`/`ExchangeWithdrawContract` transaction; no privileged role, leaked key, or malicious peer collusion is required beyond ordinary transaction-ordering/MEV capability that already exists on the TRON P2P/consensus layer. This makes the likelihood non-trivial for any exchange pair with meaningful liquidity depth relative to trade size.

### Recommendation
Add explicit minimum/maximum bound fields to `ExchangeInjectContract` and `ExchangeWithdrawContract` (analogous to `ExchangeTransactionContract.expected`), and enforce them in `ExchangeInjectActuator`/`ExchangeWithdrawActuator`'s `doValidate()`/`execute()` by comparing the computed `anotherTokenQuant` against the caller-supplied bound before mutating balances, mirroring the check already present in `ExchangeTransactionActuator`.

### Proof of Concept
1. Attacker monitors mempool for a pending `ExchangeInjectContract` (or `ExchangeWithdrawContract`) transaction from a victim targeting exchange pair `(A, B)`.
2. Attacker submits and gets included first a large trade (`ExchangeTransactionContract`) that shifts the pool's `A:B` ratio unfavorably for the victim's pending action.
3. Victim's `ExchangeInjectActuator`/`ExchangeWithdrawActuator` transaction executes using the manipulated ratio, computing `anotherTokenQuant` at lines [6](#0-5)  (or the withdraw equivalent) without any minimum guard, causing the victim to give up more (inject) or receive less (withdraw) counter-asset than intended.
4. Attacker submits a reverse trade to restore the ratio and realize the extracted value, completing the sandwich.

### Citations

**File:** Tron protobuf protocol document.md (L1394-1420)
```markdown
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L65-99)
```java
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

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());
      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, tokenQuant));
      } else {
        accountCapsule.reduceAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .reduceAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L68-89)
```java
      byte[] tokenID = exchangeWithdrawContract.getTokenId().toByteArray();
      long tokenQuant = exchangeWithdrawContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant;

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
