### Title
Unchecked long-arithmetic overflow in Exchange balance-limit validation and pool trade math can bypass TRC10 exchange safety checks - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`, `actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java`, `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`)

### Summary
The `ExchangeTransactionActuator` (and similarly `ExchangeInjectActuator`) validates a trade by adding the incoming token quantity to the exchange pool's current balance and comparing the result against `dynamicStore.getExchangeBalanceLimit()`. The addition is performed through `AbstractExchangeActuator.addExact()`, which only uses overflow-checked arithmetic (`StrictMathWrapper.addExact`) when the on-chain governance flag `allowHardenExchangeCalculation` is enabled; otherwise it silently falls back to plain Java `long` addition that wraps on overflow. [1](#0-0) [2](#0-1) 

### Finding Description
This is the same root-cause pattern as the Boba `clientDepositL1Batch()` bug: an unchecked arithmetic addition is used directly inside a `require`/bounds-style validation (`tokenBalance > balanceLimit`), so if the sum overflows and wraps into a negative (or otherwise small) number, the check silently passes even though the true pre-overflow value would have exceeded the configured safety limit.

In `ExchangeTransactionActuator.doValidate()`:
```
long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance : secondTokenBalance);
tokenBalance = addExact(tokenBalance, tokenQuant);
if (tokenBalance > balanceLimit) {
  throw new ContractValidateException(...);
}
``` [2](#0-1) 

`addExact` here resolves to `AbstractExchangeActuator.addExact`, which is only "hardened" (overflow-checked) when the network-level flag `allowHardenExchangeCalculation` is set true via proposal; by default it performs plain `x + y`:
```
public long addExact(long x, long y) {
  return allowHarden() ? StrictMathWrapper.addExact(x, y) : x + y;
}
``` [3](#0-2) 

Execution then calls `ExchangeCapsule.transaction(tokenID, tokenQuant, allowStrictMath, allowHarden())`, which similarly only performs overflow-checked balance updates (`StrictMathWrapper.addExact`/`subtractExact` and the `SafeExchangeProcessor`) when `hardenedCalc` is true; otherwise it mutates `firstTokenBalance`/`secondTokenBalance` with plain `+`/`-` and an unguarded `ExchangeProcessor`:
```
newFirstTokenBalance = hardenedCalc
    ? StrictMathWrapper.addExact(firstTokenBalance, sellTokenQuant)
    : firstTokenBalance + sellTokenQuant;
``` [4](#0-3) 

If an attacker can drive a pool's `firstTokenBalance`/`secondTokenBalance` (which are attacker-influenced via `ExchangeCreateContract`/`ExchangeInjectContract`, both of which accept attacker-chosen TRC10 token quantities) close to `Long.MAX_VALUE`, a subsequent `ExchangeTransactionContract` with a carefully chosen `quant` can wrap the sum in the `tokenBalance > balanceLimit` check to a negative value, bypassing the pool-size cap, and the same unguarded arithmetic is then used to actually update the persisted pool state and credit the attacker's account via `accountCapsule.addAssetAmountV2` / `setBalance`. [5](#0-4) 

### Impact Explanation
If reachable, this allows draining/corrupting TRC10 exchange pool balances (both TRX and TRC10 asset reserves) through pure arithmetic overflow triggered by an ordinary broadcast `ExchangeTransactionContract`, analogous to draining the L1LiquidityPool in the original report. This is an asset/accounting-corruption class issue reachable from anonymous broadcast transactions, which fits the in-scope categories (exchange/market math).

### Likelihood Explanation
The likelihood is uncertain and could not be fully confirmed with available tools:
- The vulnerable, non-hardened arithmetic path is exercised only when `allowHardenExchangeCalculation` is disabled at the network/proposal level; I could not confirm within this session whether this flag defaults to on or off on mainnet, or whether it has already been enabled network-wide (the presence of dedicated "hardened" test suites, e.g. `ExchangeTransactionActuatorTest.hardenedExecuteOverflowThrowsArithmeticException`, suggests this hardening was added specifically to close this exact class of bug).
- Reaching a balance near `Long.MAX_VALUE` requires either (a) a TRC10 asset issued with an extremely large total supply being injected into an exchange pool, or (b) many rounds of `ExchangeInjectContract`/`ExchangeTransactionContract` calls; I was unable to verify within the tool budget whether `AssetIssueActuator` or `ExchangeCreateActuator`/`ExchangeInjectActuator` impose supply/balance ceilings low enough to make this practically unreachable, or whether `getExchangeBalanceLimit()` (a governance-configurable proposal parameter) already caps balances well below overflow range in all deployed network configurations.

Because these two preconditions (hardening disabled, and balances reachable near `Long.MAX_VALUE`) could not be conclusively confirmed or ruled out, this should be treated as a plausible but unconfirmed analog requiring further verification of current mainnet proposal parameter values and asset-issuance supply limits.

### Recommendation
- Confirm the current mainnet/testnet value of the `allowHardenExchangeCalculation` proposal parameter (via `ProposalUtil`/`DynamicPropertiesStore`) and, if not already enabled, enable it or make hardened, overflow-checked arithmetic (`StrictMathWrapper.addExact`/`subtractExact`, `SafeExchangeProcessor`) unconditional in `AbstractExchangeActuator`, `ExchangeCapsule.transaction`, `ExchangeCreateActuator`, and `ExchangeInjectActuator`, rather than opt-in.
- Independently cap `getExchangeBalanceLimit()` and TRC10 total-supply values far below `Long.MAX_VALUE / 2` so that even non-hardened additions cannot wrap, providing defense-in-depth regardless of the proposal flag state.
- Add explicit overflow assertions in `doValidate()` prior to any use of the summed value in a comparison, independent of the `allowHarden()` flag.

### Proof of Concept
Conceptual (arithmetic-level, not fully validated end-to-end due to unresolved supply-limit preconditions above):
1. With `allowHardenExchangeCalculation` disabled, attacker issues/accumulates a TRC10 asset balance or injects it into an `Exchange` pool such that `secondTokenBalance` (or `firstTokenBalance`) approaches `Long.MAX_VALUE`. [6](#0-5) 
2. Attacker broadcasts an `ExchangeTransactionContract` with `quant` chosen so `tokenBalance + quant` (plain `long` addition in `ExchangeTransactionActuator.doValidate`) overflows past `Long.MAX_VALUE`, wrapping to a negative number and passing the `tokenBalance > balanceLimit` check. [2](#0-1) 
3. `ExchangeCapsule.transaction()` executes with the same non-hardened arithmetic, updating pool reserves and crediting the attacker via `accountCapsule.addAssetAmountV2`/`setBalance` beyond the intended pool-size safety limit. [7](#0-6)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-23)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }

  public long subtractExact(long x, long y) {
    return allowHarden() ? StrictMathWrapper.subtractExact(x, y) : x - y;
  }

  public long addExact(long x, long y) {
    return allowHarden() ? StrictMathWrapper.addExact(x, y) : x + y;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L80-97)
```java
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

```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L199-205)
```java
    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance
        : secondTokenBalance);
    tokenBalance = addExact(tokenBalance, tokenQuant);
    if (tokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-166)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-236)
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

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (newTokenBalance > balanceLimit || newAnotherTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```
