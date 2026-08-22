Confirmed: `ExchangeWithdrawContract` (unlike `ExchangeTransactionContract`) has no `expected`/minimum-output field at all, and `ExchangeWithdrawActuator` computes `anotherTokenQuant` purely from the live pool ratio at execution time [1](#0-0) , with the only extra check being a rounding-precision check, not a slippage/price-protection check [2](#0-1) . This is the strongest, closest analog to the reported bug class.

### Title
Missing slippage protection in `ExchangeWithdrawContract` enables sandwich attacks against Bancor-style exchange withdrawals - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
The TRON on-chain Bancor-like exchange (`ExchangeCapsule`) lets an exchange creator withdraw liquidity via `ExchangeWithdrawContract`/`ExchangeWithdrawActuator`. Unlike the sibling `ExchangeTransactionContract` (trade execution), which enforces a caller-supplied minimum-received (`expected`) value [3](#0-2) , `ExchangeWithdrawContract` has no equivalent minimum-output/slippage parameter. This mirrors exactly the root cause described in the external report: an automatic swap/settlement priced off pool state at execution time with no caller-enforced bound, letting anyone reprice the pool immediately beforehand.

### Finding Description
`ExchangeWithdrawActuator.execute` computes the counter-token amount to pay out (`anotherTokenQuant`) strictly as a function of the exchange's current `firstTokenBalance`/`secondTokenBalance` ratio and the withdrawer-specified `tokenQuant`, with no minimum-expected bound supplied by the transaction sender: [4](#0-3) . The validation path (`doValidate`) performs the same ratio computation and only checks that the result is internally consistent/precise (a rounding-drift check between double and BigDecimal math), never that it exceeds a user-defined floor: [5](#0-4) .

Because any account can submit `ExchangeTransactionContract` trades against the same exchange pool (subject only to the caller's own `expected` bound on that trade, which is trivially satisfiable), an attacker who observes a pending `ExchangeWithdrawContract` transaction in the mempool can:
1. Front-run it with a large trade that skews the `firstTokenBalance`/`secondTokenBalance` ratio unfavorably for the withdrawer.
2. Let the victim's withdraw execute at the manipulated ratio, receiving far less of `anotherTokenID` than expected.
3. Back-run with a reverse trade to restore the ratio and capture the value extracted from the victim.

This is architecturally identical to the reported Voting.sol issue: an internal "swap" (asset conversion) is executed automatically, priced from mutable on-chain state, without a slippage/minimum-output guard set by the party bearing the price risk.

### Impact Explanation
A successful sandwich against `ExchangeWithdrawContract` directly transfers value from the withdrawing exchange creator to the attacker, corrupting expected token accounting for that account with no compensating control. Since java-tron's exchange feature is a native, permissionless, on-chain AMM-like primitive reachable from any broadcast transaction, this is an unprivileged asset/accounting-corruption issue in the exchange/market math category.

### Likelihood Explanation
Exploitation only requires the attacker to observe a pending `ExchangeWithdrawContract` transaction (visible in the transaction pool before block inclusion) and to already hold (or acquire) tokens in the target pair — no special permissions, keys, or node access are needed. Any active TRON exchange pair with the withdrawer holding a non-trivial balance is a viable target, making this readily reachable by an ordinary, anonymous broadcaster.

### Recommendation
Add a caller-supplied minimum-expected-output field to `ExchangeWithdrawContract` (analogous to `expected` in `ExchangeTransactionContract`), and enforce in `ExchangeWithdrawActuator.doValidate`/`execute` that the computed `anotherTokenQuant` is not less than that minimum, aborting the transaction otherwise.

### Proof of Concept
1. Attacker monitors the mempool for an `ExchangeWithdrawContract` from victim V against exchange `E` (pair A/B), withdrawing `tokenQuant` of token A.
2. Attacker broadcasts a large `ExchangeTransactionContract` selling token B into `E` (or buying A) with a self-satisfying `expected` bound, shifting `firstTokenBalance`/`secondTokenBalance` so that A becomes relatively more concentrated versus B (see ratio math at [6](#0-5) ), with high priority/fee to land first.
3. V's `ExchangeWithdrawContract` executes at the skewed ratio, receiving less `anotherTokenQuant` (token B) than it would have at the pre-attack ratio.
4. Attacker broadcasts a reverse `ExchangeTransactionContract` to restore the ratio and realize the difference as profit, extracted from V's withdrawal.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L68-90)
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
