Now I have enough evidence to complete the analysis.

### Title
Sandwichable exchange liquidity injection/withdrawal in TRC10 Bancor-style exchange lacks slippage protection - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
Java-tron's TRC10 `Exchange` module implements a Bancor-style constant-relay AMM (`ExchangeCapsule.transaction`, backed by `ExchangeProcessor`/`SafeExchangeProcessor`) alongside `ExchangeInjectContract` (add liquidity) and `ExchangeWithdrawContract` (remove liquidity). Unlike `ExchangeTransactionContract` (swap), which carries an `expected` minimum-output parameter enforced in validation, neither `ExchangeInjectContract` nor `ExchangeWithdrawContract` carries any max/min bound on the paired token amount computed at execution time from the *current* pool balances. This mirrors the Portfolio.sol `allocate`/`deallocate` bug: an attacker can sandwich a victim's inject/withdraw transaction with `ExchangeTransactionContract` swaps to skew `firstTokenBalance`/`secondTokenBalance`, forcing the victim to inject or withdraw at a manipulated ratio, and then reverse the swap to extract profit, all while the exchange creator involuntarily gets bad-priced liquidity execution.

### Finding Description
`ExchangeInjectContract`/`ExchangeWithdrawContract` only specify a single `token_id` and `quant` [1](#0-0) . The paired `anotherTokenQuant` is computed purely from the pool's live `firstTokenBalance`/`secondTokenBalance` ratio at execution time in both the inject actuator [2](#0-1)  and the withdraw actuator [3](#0-2) . Validation for both only re-derives the same ratio-based amount and checks balance sufficiency / balance limits / precision, but never lets the caller cap or floor the paired amount [4](#0-3) .

By contrast, `ExchangeTransactionContract` (the swap path) explicitly carries an `expected` field and enforces `anotherTokenQuant >= tokenExpected` in `doValidate`, which is exactly the slippage protection the original report says was missing and later added in the referenced fix [5](#0-4) . No equivalent bound exists for `ExchangeInjectActuator` or `ExchangeWithdrawActuator`, which is the direct structural analog of the Portfolio.sol `allocate`/`deallocate` finding: the pool's reserve ratio (analogous to `virtualX`/`virtualY`) can be moved by an unprivileged pre/post swap using `ExchangeTransactionContract`, sandwiching the inject/withdraw transaction.

Both any account can call `ExchangeTransactionContract` to move the ratio (fully unprivileged, broadcast-transaction-reachable) — the actuator's `doValidate` performs no ownership check on swaps [6](#0-5) . Only the exchange creator can call inject/withdraw (checked via `!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())`), but that check restricts who the *victim* can be, not who the *attacker* is — the sandwiching swaps that manipulate price and extract profit are executed by an arbitrary unprivileged account, exactly matching the original MEV-searcher threat model where the LP is the victim of price manipulation caused by unprivileged swap transactions.

### Impact Explanation
An unprivileged attacker can front-run and back-run any `ExchangeInjectContract`/`ExchangeWithdrawContract` transaction with `ExchangeTransactionContract` swaps to move `firstTokenBalance`/`secondTokenBalance` before the inject/withdraw executes, then reverse the swap afterward. This forces the exchange creator (victim) to inject or withdraw tokens at a manipulated ratio, transferring value from the victim to the attacker — an asset/accounting-corruption impact on TRC10 exchange liquidity providers, analogous to the original RMM finding.

### Likelihood Explanation
The attack requires no special privilege — any account can submit `ExchangeTransactionContract` swap transactions and sequence them around a known pending inject/withdraw transaction (e.g., via mempool observation), which is a well-established and low-cost MEV pattern on TRON given the visible transaction pool.

### Recommendation
Add optional bound parameters (e.g., `expected`/`min_expected` for withdraw, `max` cap for inject) to `ExchangeInjectContract` and `ExchangeWithdrawContract`, and enforce them in `ExchangeInjectActuator#doValidate` / `ExchangeWithdrawActuator#doValidate` analogous to the `tokenExpected` check already present in `ExchangeTransactionActuator`, so liquidity providers can bound the paired token amount and the transaction reverts if the pool ratio has been manipulated beyond the specified tolerance.

### Proof of Concept
1. Exchange creator submits `ExchangeInjectContract` to add liquidity with `token_id`/`quant` only, no bound on the paired amount.
2. Attacker observes this pending transaction and front-runs it with `ExchangeTransactionContract` selling asset A into the pool, skewing `firstTokenBalance`/`secondTokenBalance`.
3. Creator's inject executes, computing `anotherTokenQuant` from the skewed ratio via `floorDiv(multiplyExact(secondTokenBalance, tokenQuant), firstTokenBalance)` [7](#0-6) , forcing the creator to deposit an unfavorable amount of the paired token.
4. Attacker back-runs with the reverse `ExchangeTransactionContract` swap, restoring the ratio and pocketing the difference, with no validation anywhere in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` to detect or reject this manipulation.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L106-112)
```java
  public long getFirstTokenBalance() {
    return this.exchange.getFirstTokenBalance();
  }

  public long getSecondTokenBalance() {
    return this.exchange.getSecondTokenBalance();
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L142-216)
```java
    byte[] ownerAddress = contract.getOwnerAddress().toByteArray();
    String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);

    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }

    if (!accountStore.has(ownerAddress)) {
      throw new ContractValidateException("account[" + readableOwnerAddress + NOT_EXIST_STR);
    }

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);

    if (accountCapsule.getBalance() < calcFee()) {
      throw new ContractValidateException("No enough balance for exchange transaction fee!");
    }

    ExchangeCapsule exchangeCapsule;
    try {
      exchangeCapsule = Commons.getExchangeStoreFinal(dynamicStore, exchangeStore, exchangeV2Store)
          .get(ByteArray.fromLong(contract.getExchangeId()));
    } catch (ItemNotFoundException ex) {
      throw new ContractValidateException("Exchange[" + contract.getExchangeId()
          + ActuatorConstant.NOT_EXIST_STR);
    }

    byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
    byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
    long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
    long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

    byte[] tokenID = contract.getTokenId().toByteArray();
    long tokenQuant = contract.getQuant();
    long tokenExpected = contract.getExpected();

    if (dynamicStore.getAllowSameTokenName() == 1
        && !Arrays.equals(tokenID, TRX_SYMBOL_BYTES)
        && !isNumber(tokenID)) {
      throw new ContractValidateException("token id is not a valid number");
    }
    if (!Arrays.equals(tokenID, firstTokenID) && !Arrays.equals(tokenID, secondTokenID)) {
      throw new ContractValidateException("token is not in exchange");
    }

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

```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
