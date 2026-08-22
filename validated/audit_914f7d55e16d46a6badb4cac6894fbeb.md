### Title
Missing exchange-pool invariant check in legacy (non-hardened) `ExchangeCapsule.transaction()` path allows negative token balances - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`)

### Summary
`ExchangeCapsule.transaction()` computes new pool balances (`newFirstTokenBalance` / `newSecondTokenBalance`) for an AMM-style bancor-formula swap, but only validates that these balances remain non-negative when `hardenedCalc` (i.e. the `ALLOW_HARDEN_EXCHANGE_CALCULATION` chain parameter) is enabled. When that flag is disabled — which is a normal, committee-controlled chain configuration state, not a privileged/attacker-controlled path — no invariant check is performed at all, mirroring the reported bug class where `_checkBalances()` is applied on some code paths (`_swap()`, `_lpTokenSpecified()`) but omitted on others (`_reserveTokenSpecified()`, reached via `depositGivenInputAmount()`/`withdrawGivenOutputAmount()`).

### Finding Description
`ExchangeCapsule.transaction(byte[] sellTokenID, long sellTokenQuant, boolean useStrictMath, boolean hardenedCalc)` computes `buyTokenQuant` via the bancor-like `ExchangeProcessor`/`SafeExchangeProcessor` and derives the new pool balances: [1](#0-0) 

The resulting balances are only checked for negativity when `hardenedCalc` is `true`: [2](#0-1) 

`hardenedCalc` is derived from `AbstractExchangeActuator.allowHarden()`, which reads the `ALLOW_HARDEN_EXCHANGE_CALCULATION` dynamic property: [3](#0-2) 

This value defaults to disabled/legacy behavior and is toggled only via a governance proposal (`ProposalUtil`/`ProposalService`), meaning for any period the proposal is not activated (including potentially the entire history prior to activation, or after a temporary rollback), every `ExchangeTransactionContract` broadcast by any ordinary user runs through the legacy branch with **no post-computation balance/invariant check** — exactly analogous to the reported Shell issue where `_checkBalances()` was omitted on a reachable calculation path.

`ExchangeTransactionActuator.execute()` calls `exchangeCapsule.transaction(tokenID, tokenQuant, dynamicStore.allowStrictMath(), allowHarden())` and unconditionally persists the resulting (possibly invariant-violating) balances via `Commons.putExchangeCapsule(...)`: [4](#0-3) 

The pre-execution `doValidate()` in the actuator does not independently bound-check the resulting balances either — it only checks `anotherTokenQuant < tokenExpected` and a `balanceLimit` on the *increasing* side of the swap, not the decreasing side: [5](#0-4) 

So the only genuine invariant guard for negative pool balances lives entirely inside the `hardenedCalc == true` branch of `ExchangeCapsule.transaction()`, structurally identical to the Shell bug where the invariant check (`_checkBalances`) exists in some call paths (swap/lp) but is missing on others (deposit/withdraw via `_reserveTokenSpecified`).

### Impact Explanation
If the legacy (non-hardened) path is active (the default/normal chain state before the hardening proposal is activated, or if it is deactivated again), a swap using the double-precision `ExchangeProcessor` bancor formula can, due to floating-point rounding/precision behavior, produce a `buyTokenQuant` that exceeds the current opposite-side pool balance. With no invariant check in that branch, `newFirstTokenBalance` or `newSecondTokenBalance` can go negative and is persisted directly into the on-chain `ExchangeCapsule`/`Exchange` proto (`setFirstTokenBalance`/`setSecondTokenBalance`), corrupting exchange pool accounting for all subsequent swap/inject/withdraw participants (asset/accounting corruption, potential permanent DoS of the exchange pool, and value extraction by draining a pool side below zero equivalent, i.e. giving out more of a token than physically backs the pool).

### Likelihood Explanation
The vulnerable path is reached by any anonymous user broadcasting a standard `ExchangeTransactionContract` transaction (`wallet/exchangetransaction`) against any existing V1/V2 exchange pool — no privileged role, leaked key, or malicious peer/P2P involvement is required. The only precondition is that `allowHardenExchangeCalculation` is not enabled, which is the network's ordinary/default operating mode unless and until the corresponding chain proposal is activated by SRs. This satisfies the "unprivileged, RPC/broadcast-reachable" requirement.

### Recommendation
Move the balance/invariant check (`newFirstTokenBalance < 0 || newSecondTokenBalance < 0`, or equivalently `_checkBalances`-style bound checks) outside the `hardenedCalc` conditional so it always executes regardless of the `ALLOW_HARDEN_EXCHANGE_CALCULATION` flag, and throw `ContractValidateException` before mutating `this.exchange`. Additionally, `ExchangeTransactionActuator.doValidate()` should independently verify that the computed `buyTokenQuant` does not exceed the current opposite-side token balance before allowing execution, so validation and execution enforce the same invariant.

### Proof of Concept
Exact reproduction requires driving the double-precision bancor computation in `ExchangeProcessor.exchange()` to a rounding edge case where `exchangeFromSupply` returns a value greater than or equal to the current `buyTokenBalance`, while `allowHardenExchangeCalculation` is disabled (default). Concretely:
1. Ensure `ALLOW_HARDEN_EXCHANGE_CALCULATION` is not activated (default network state) so `allowHarden()` returns `false`.
2. Create/find an `Exchange` pool with a small `secondTokenBalance` relative to `firstTokenBalance` (extreme balance ratio) such that the bancor formula's double-precision output for `exchangeFromSupply` rounds to a value ≥ `secondTokenBalance`.
3. Broadcast an `ExchangeTransactionContract` (via `wallet/exchangetransaction`) selling the first token for the full/near-full amount of the second token's balance.
4. Observe `ExchangeCapsule.transaction()` returns `buyTokenQuant >= secondTokenBalance`; since `hardenedCalc` is `false`, the `< 0` check at lines 160–162 is skipped, and `newSecondTokenBalance` (now negative) is committed via `Commons.putExchangeCapsule(...)` in `ExchangeTransactionActuator.execute()`, corrupting the pool state.

Exact numeric parameters that trigger the floating-point edge case were not independently reverified against the live `Maths.pow`/`StrictMathWrapper.pow` implementation in this session; a Devin session with test-execution access should construct/run a targeted case analogous to `ExchangeProcessorTest` (e.g. sweeping extreme balance ratios and small quantities) to confirm a concrete negative-balance trigger before filing.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L160-166)
```java
    if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
      throw new ContractValidateException("Exchange balance must be >=0 after transaction");
    }
    this.exchange = this.exchange.toBuilder()
        .setFirstTokenBalance(newFirstTokenBalance)
        .setSecondTokenBalance(newSecondTokenBalance)
        .build();
```

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-15)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-99)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());

      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
      } else {
        anotherTokenID = firstTokenID;
      }

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());
      accountCapsule.setBalance(newBalance);

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

      ret.setExchangeReceivedAmount(anotherTokenQuant);
      ret.setStatus(fee, code.SUCESS);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L199-221)
```java
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

    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
