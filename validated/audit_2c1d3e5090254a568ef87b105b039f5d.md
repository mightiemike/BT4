### Title
Integer division rounding to zero in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` can block legitimate exchange liquidity operations - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
Both `ExchangeInjectActuator.doValidate()` and `ExchangeWithdrawActuator.doValidate()` compute the counterpart token amount (`anotherTokenQuant`) with an integer division of the exchange pool balances. When the pool ratio is skewed (e.g. because one side is a low-precision asset created via `AssetIssueActuator` with `precision == 0`, or the pool has grown very large relative to the injected/withdrawn quantity), this division truncates to zero, and the actuator reverts with a hard-coded "must be greater than 0" check — exactly the `streamAmt > 0` pattern described in the Locke.sol report.

### Finding Description
`ExchangeInjectActuator.doValidate()` computes: [1](#0-0) 

and `ExchangeWithdrawActuator.doValidate()` performs the analogous integer-division computation: [2](#0-1) 

In both cases `anotherTokenQuant` is derived by multiplying one pool balance by the input `tokenQuant` and integer-dividing by the other pool balance (`BigInteger.divide()`/`divideToIntegralValue()`, which truncate toward zero). If `tokenQuant` is small relative to the balance ratio — which becomes increasingly likely the larger the pool grows (analogous to "long stream duration") or the coarser the token's precision is (analogous to "low decimal token"), since `AssetIssueActuator` allows tokens with `precision == 0`: [3](#0-2) 

then `anotherTokenQuant` rounds down to `0`, and the actuator explicitly reverts:
- Inject: `"the calculated token quant must be greater than 0"`
- Withdraw: `"withdraw another token quant must greater than zero"`

This mirrors the Locke.sol root cause precisely: a division whose granularity is a function of a small numerator/large denominator ratio truncates to zero, and a defensive `> 0` check turns that truncation into an outright revert rather than gracefully handling the edge case (e.g. by using higher internal precision or rejecting only when truly impossible).

### Impact Explanation
The account holding the exchange (any regular TRON account that called `ExchangeCreateActuator` to create the pool — this is not a privileged/witness role, any user can create and own an exchange) can be transiently blocked from injecting or withdrawing liquidity whenever the pool ratio and requested quant combination produces a zero counterpart amount. This is a temporary denial-of-service on that user's own liquidity management operations: they cannot inject/withdraw until they pick a larger `tokenQuant` or the pool ratio changes, which matches the "Medium risk / temporarily blocked" classification of the original report rather than a permanent fund lock.

### Likelihood Explanation
Likelihood is non-trivial but not high: it requires the exchange pool balances to be sufficiently imbalanced (achievable by creating a token with `precision == 0` paired against TRX/another asset, since `AssetIssueContract` explicitly allows zero precision) combined with a small injected/withdrawn quantity relative to that imbalance. Both conditions are fully controllable by an ordinary unprivileged user setting up their own exchange, so the scenario is realistically reachable, though it self-resolves once the caller adjusts the amount.

### Recommendation
Scale intermediate calculations to a fixed higher-precision base (similar to the `SafeExchangeProcessor`'s use of `1_000_000_000_000_000_000L` supply and `BigDecimal` with explicit scale) before dividing down to the token's native precision, so that legitimate small inject/withdraw requests are not spuriously rejected due to integer-division truncation. Alternatively, return a clear, actionable minimum-quant requirement instead of a bare revert.

### Proof of Concept
1. Create asset `X` with `precision = 0` via `AssetIssueActuator`. [3](#0-2) 
2. Create an exchange pairing `X` and TRX via `ExchangeCreateActuator`, then inject a large TRX amount relative to `X` (e.g. `firstTokenBalance(X) = 10`, `secondTokenBalance(TRX) = 1_000_000_000`).
3. Call `ExchangeInjectContract` with `tokenId = X`, `quant = 1` (a legitimate small inject of the low-precision token).
4. `anotherTokenQuant = secondTokenBalance * tokenQuant / firstTokenBalance = 1_000_000_000 * 1 / 10 = 100_000_000` — succeeds in this ratio, but flip the direction: inject TRX with `quant` small enough that `firstTokenBalance(X) * tokenQuant / secondTokenBalance(TRX)` truncates to `0` (e.g. `quant = 5` when `firstTokenBalance = 10`, `secondTokenBalance = 1_000_000_001`), triggering: [4](#0-3) 
which reverts the transaction and prevents the user from performing that inject until they increase the quant, demonstrating the same zero-division block class as the reported Locke.sol `streamAmt == 0` issue.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-231)
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

    if (anotherTokenQuant <= 0) {
      throw new ContractValidateException("the calculated token quant  must be greater than 0");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L214-227)
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
