## Title
Permissionless Exchange creation allows the first "depositor" to set an arbitrarily skewed token ratio, forcing later traders into unfavorable/impossible-to-round trades - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java`)

### Summary
`ExchangeCreateActuator` is java-tron's analog of a permissionless liquidity pool: any unprivileged account can call it to create a bonding-curve `Exchange` between two arbitrary TRC10 tokens/TRX by depositing whatever `firstTokenBalance`/`secondTokenBalance` it chooses. [1](#0-0)  This is structurally identical to the reported ERC4626 issue: the entity that makes the first deposit unilaterally fixes the "price" (here, the token exchange ratio) that all subsequent participants must trade against, and the contract enforces almost no constraint on how skewed that ratio can be.

### Finding Description
`doValidate()` in `ExchangeCreateActuator` only checks that both balances are `> 0` and each individually below `getExchangeBalanceLimit()` — it never checks the *ratio* between the two balances: [2](#0-1) 

Any account can therefore create an exchange with an extremely skewed ratio, e.g. `firstTokenBalance = balanceLimit`, `secondTokenBalance = 1`. This sets the internal Bancor-style relay "supply" and price used by `ExchangeCapsule.transaction()`/`ExchangeProcessor`, which is invoked by every subsequent trade through `ExchangeTransactionActuator`: [3](#0-2) [4](#0-3) 

Because `exchangeFromSupply` truncates to a `long` (`(long) exchangeBalance`), any trade against the thin side of a skewed pool computes a proportionally tiny/possibly-zero output for the same input size a balanced pool would use. This mirrors the ERC4626 issue exactly: the first "depositor" (pool creator) fixes the price/ratio, and any later user wanting a non-trivial output is "forced ... to deposit huge value" (a correspondingly huge input) to get any meaningful return, exactly as described in the source report's impact section. Additionally, only the original creator is permitted to `ExchangeInjectActuator` more liquidity to correct the ratio: [5](#0-4) 
so no other market participant can ever normalize a skewed pool — they can only trade against it, worsening the imbalance further, or abandon it.

### Impact Explanation
Ordinary users who are unaware an exchange was created with a skewed ratio will receive drastically less than expected for a given input, or their trade will revert (`"token required must greater than expected"`) unless they submit unrealistically large `tokenQuant` values to get non-negligible output — directly reproducing the reported impact where "future depositors are forced [to trade/deposit] huge value... which not all users can do." Because `ExchangeCreateContract` is fully permissionless and requires no elevated role, this fits the "unprivileged-user analog" criterion (any account, no admin/witness privilege, can create a misleadingly-priced public market).

### Likelihood Explanation
Creating a skewed exchange requires only enough balance to satisfy `firstTokenBalance <= balanceLimit` and `secondTokenBalance <= balanceLimit`, both of which are attacker-controlled and can be made arbitrarily lopsided (e.g., `balanceLimit` vs `1`). No governance or witness approval is needed to create an `Exchange`, so likelihood is high for any token pair a malicious or careless user wants to list first.

### Recommendation
Add a minimum-ratio / minimum-balance sanity check in `ExchangeCreateActuator.doValidate()` (e.g., require a maximum ratio bound between `firstTokenBalance` and `secondTokenBalance`, or a minimum absolute balance on both sides) so that newly created exchanges cannot be initialized with economically degenerate ratios, analogous to enforcing a minimum-liquidity/burn-share requirement for ERC4626 vault initialization.

### Proof of Concept
1. Attacker calls `ExchangeCreateContract` with `firstTokenId = TRX`, `firstTokenBalance = 1`, `secondTokenId = tokenX`, `secondTokenBalance = dynamicStore.getExchangeBalanceLimit()` (or vice versa) — this passes all `doValidate()` checks in `ExchangeCreateActuator` (lines 201–208). [6](#0-5) 
2. A victim later calls `ExchangeTransactionContract` to sell a normal amount of TRX for `tokenX`. `ExchangeCapsule.transaction()`/`ExchangeProcessor.exchange()` computes an output rounded down to a value far below the fair-market rate because of the extreme initial ratio. [7](#0-6) 
3. The victim either receives a negligible amount, or must supply a disproportionately large `tokenQuant` to obtain a usable amount of `tokenX`, reproducing the "future depositors forced to deposit huge value" impact from the reported vulnerability class. Only the original creator can rebalance via `ExchangeInjectActuator` (creator-only restriction), so no other party can fix the skew. [5](#0-4)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L55-76)
```java
      byte[] firstTokenID = exchangeCreateContract.getFirstTokenId().toByteArray();
      byte[] secondTokenID = exchangeCreateContract.getSecondTokenId().toByteArray();
      long firstTokenBalance = exchangeCreateContract.getFirstTokenBalance();
      long secondTokenBalance = exchangeCreateContract.getSecondTokenBalance();

      long newBalance = subtractExact(accountCapsule.getBalance(), fee);

      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(firstTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, firstTokenBalance));
      } else {
        accountCapsule
            .reduceAssetAmountV2(firstTokenID, firstTokenBalance, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(secondTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, secondTokenBalance));
      } else {
        accountCapsule
            .reduceAssetAmountV2(secondTokenID, secondTokenBalance, dynamicStore, assetIssueStore);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L197-208)
```java
    if (Arrays.equals(firstTokenID, secondTokenID)) {
      throw new ContractValidateException("cannot exchange same tokens");
    }

    if (firstTokenBalance <= 0 || secondTokenBalance <= 0) {
      throw new ContractValidateException("token balance must greater than zero");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (firstTokenBalance > balanceLimit || secondTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L17-45)
```java
  private long exchangeToSupply(long balance, long quant) {
    logger.debug("balance: " + balance);
    long newBalance = balance + quant;
    logger.debug("balance + quant: " + newBalance);

    double issuedSupply = -supply * (1.0
        - Maths.pow(1.0 + (double) quant / newBalance, 0.0005, this.useStrictMath));
    logger.debug("issuedSupply: " + issuedSupply);
    long out = (long) issuedSupply;
    supply += out;

    return out;
  }

  private long exchangeFromSupply(long balance, long supplyQuant) {
    supply -= supplyQuant;

    double exchangeBalance = balance
        * (Maths.pow(1.0 + (double) supplyQuant / supply, 2000.0, this.useStrictMath) - 1.0);
    logger.debug("exchangeBalance: " + exchangeBalance);

    return (long) exchangeBalance;
  }

  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    long relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L175-177)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```
