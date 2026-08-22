### Title
Floating-point precision downgrade in `ExchangeWithdrawActuator` allows imprecise (rug-style) drains of Bancor-style exchange pool reserves - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
`ExchangeWithdrawActuator.doValidate()` enforces a "precision" sanity check on how much of the paired token an exchange withdrawal should release, to keep the withdrawal consistent with the constant-ratio (Bancor-like) invariant of the on-chain exchange pool. When the chain-wide feature flag `allowHardenExchangeCalculation` has not been activated by governance (its non-default, opt-in state), this check is computed using Java `double` arithmetic instead of `BigDecimal`, exposing the check to IEEE‑754 floating point rounding error for large token-balance values.

### Finding Description
In `doValidate()`, the amount of the counter-token that must accompany a withdrawal (`anotherTokenQuant`) is first computed precisely with `BigDecimal`/integer division: [1](#0-0) 

The "is this withdrawal ratio close enough to the pool's true ratio" check then branches on `allowHarden()`: [2](#0-1) 

When `allowHarden` is `false` (the pre-activation / legacy behavior), the check is:
```java
double remainder = bigSecondTokenBalance.multiply(bigTokenQuant)
    .divide(bigFirstTokenBalance, 4, BigDecimal.ROUND_HALF_UP).doubleValue()
    - anotherTokenQuant;
if (remainder / anotherTokenQuant > 0.0001) {
  throw new ContractValidateException("Not precise enough");
}
```
`double` has only ~15-17 significant decimal digits of precision. TRON token/TRX balances routinely use `long` values that can exceed `2^53` (≈9×10^15) in the raw sun/quantity units used for large pools, at which point converting a `BigDecimal` to `double` (`.doubleValue()`) silently loses precision. An attacker who controls both sides of the arithmetic (they choose `tokenQuant` in the `ExchangeWithdrawContract`, and the pool balances are on-chain and readable) can select a `tokenQuant` for which:
- the true (`BigDecimal`) ratio deviation exceeds the intended 0.01% tolerance (i.e., it *should* be rejected), but
- the `double`-computed deviation rounds to a value at or below the 0.0001 threshold due to floating-point error, so the check passes.

The `allowHarden()` flag itself is gated by governance/committee proposal (`allowHardenExchangeCalculation()` in `DynamicPropertiesStore`) via `AbstractExchangeActuator.allowHarden()`: [3](#0-2)  — meaning any chain that has not yet enabled hardening (or any historical block processed before hardening was enabled) executes the imprecise path unconditionally for every `ExchangeWithdrawContract`, and this path is reachable by any account that is the creator of an on-chain `Exchange` object, which any account can create via `ExchangeCreateContract` (no admin/witness/committee privilege required, satisfying the "unprivileged" requirement).

### Impact Explanation
The precision check exists specifically to prevent a withdrawal from silently deviating from the pool's constant-product/ratio invariant. Bypassing it via floating-point rounding lets an exchange creator repeatedly submit withdrawals whose real ratio deviates from the true reserve ratio beyond the intended tolerance, corrupting the recorded `firstTokenBalance`/`secondTokenBalance` state relative to the true backing of the pool. Because other participants (`ExchangeTransactionActuator`/`MarketSellAssetActuator` style trades, and the exchange creator itself on subsequent trades) rely on these balances to price trades and settle their own token/TRX exchanges, sustained exploitation can be used to drain economically favorable amounts from the pool relative to its stated reserves — the same "unconditional/uncontrolled fund removal from a shared pool" bug class described in the report (there `gib()` let a privileged role pull funds from a vault without a collateral-ratio check; here an ordinary exchange creator can pull funds from the shared AMM reserve without an accurate ratio check).

### Likelihood Explanation
Exploitation requires only: (1) creating an `Exchange` (an unprivileged, permissionless action), and (2) crafting a `tokenQuant` value large enough that `double` precision loss becomes material (values in the sun-scale `long` range routinely exceed `2^53`). No signature bypass, no privileged role, and no additional preconditions besides normal broadcast-transaction access are needed, making this reachable directly via an ordinary signed transaction (`ExchangeWithdrawContract`) whenever the chain has not enabled `allowHardenExchangeCalculation`.

### Recommendation
Remove the `double`-based branch entirely and always perform the precision check with `BigDecimal` (as already done in the `allowHarden` branch), regardless of the `allowHardenExchangeCalculation` proposal state, or make the hardened calculation mandatory rather than opt-in via governance proposal.

### Proof of Concept
1. Create an `Exchange` with a large `firstTokenBalance`/`secondTokenBalance` pair (values whose product/ratio exceeds `2^53` when represented as `double`).
2. On a network where `allowHardenExchangeCalculation` has not been enabled (default/legacy state), submit `ExchangeWithdrawContract` transactions choosing `tokenQuant` values where:
   - `BigDecimal` remainder ratio > 0.0001 (should fail), but
   - `double`-computed remainder ratio ≤ 0.0001 (passes) due to floating-point rounding of the large `BigDecimal` values via `.doubleValue()`.
3. The transaction succeeds via `ExchangeWithdrawActuator.execute()` at [4](#0-3) , releasing tokens at a ratio that has escaped the intended tolerance check, corrupting the pool's stored balances relative to their true backing.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L74-97)
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

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, tokenQuant));
      } else {
        accountCapsule.addAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L218-227)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L228-243)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-15)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }
```
