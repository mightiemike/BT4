## Title
Exchange liquidity injection (`ExchangeInjectContract`) lacks slippage/ratio protection, enabling a sandwich attack against liquidity providers - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
java-tron's Bancor-style TRC10 "Exchange" module lets a user create a two-token liquidity pool (`ExchangeCreateContract`) and lets **that pool's creator** add/remove liquidity via `ExchangeInjectContract` / `ExchangeWithdrawContract`, while **any** account can permissionlessly swap against the pool via `ExchangeTransactionContract`. `ExchangeInjectActuator.execute`/`doValidate` computes the paired "another token" amount purely from the pool's *current* reserve ratio at execution time, with no minimum-output or maximum-deviation parameter supplied by the caller. This is the same missing-slippage-protection root cause described in the formPOL/`depositLiquidityAndIncreaseShare` report: an attacker can skew the pool ratio immediately before the creator's inject transaction lands, causing the deposit to be priced at a manipulated ratio, then reverse the skew for profit.

### Finding Description
`ExchangeInjectActuator.execute` (and the mirrored calculation in `doValidate`) derives `anotherTokenQuant` strictly from the exchange's on-chain reserves at the moment the transaction executes: [1](#0-0) 

Unlike `ExchangeTransactionContract`, which carries an `expected` field used as a minimum-received guard for swaps, `ExchangeInjectContract` has no equivalent field: [2](#0-1) 
The relevant proto definitions (`ExchangeInjectContract` vs `ExchangeTransactionContract`) confirm this asymmetry — inject/withdraw carry only `owner_address`, `exchange_id`, `token_id`, `quant`, while only the transaction (swap) contract carries `expected`.

Because any account can freely call `ExchangeTransactionContract` against the same pool (it is the permissionless swap path), an attacker can:
1. Observe a pending `ExchangeInjectContract` from the pool creator in the mempool.
2. Submit a swap that pushes the pool reserves away from the creator's expected ratio, ordered to land right before the inject transaction.
3. Let the creator's inject execute at the skewed ratio — `ExchangeInjectActuator` blindly trusts `firstTokenBalance`/`secondTokenBalance` at execution time with no external reference price or deviation bound.
4. Reverse the swap afterward to lock in profit taken from the value the creator just deposited at the wrong ratio.

`ExchangeWithdrawActuator.doValidate` has a "Not precise enough" sanity check, but it only verifies internal arithmetic precision against the *same*, potentially-manipulated on-chain ratio — it is not a slippage bound against an external/expected price, so it provides no real protection either: [3](#0-2) 

`ExchangeInjectActuator` has no analogous check at all.

### Impact Explanation
This directly reproduces the reported bug class: loss of funds for the party depositing liquidity (here, the exchange creator, who is an ordinary unprivileged account — anyone can create an exchange via `ExchangeCreateContract`) due to missing slippage/deviation protection on a liquidity-management operation, while the exploiting party is an ordinary unprivileged account using the permissionless swap path (`ExchangeTransactionContract`). This is an accounting/settlement impact: the creator's deposited tokens are converted at a manipulated ratio and the attacker extracts value from the pool, which the protocol's TRC10 accounting (`AccountCapsule` asset/TRX balances) faithfully — but incorrectly — settles.

### Likelihood Explanation
Exploitability requires only visibility into the pending inject transaction and the ability to get a swap transaction ordered immediately before (and after) it within the ~3-second block production window, which is a realistic MEV-style condition in a DPoS chain where block-producing Super Representatives (or anyone with mempool visibility) control transaction ordering within blocks. No privileged role, contract owner permission, or committee flag is required to execute the sandwiching swaps — `ExchangeTransactionContract` is fully permissionless once `supportAllowMarketTransaction`/exchange trading is enabled. The only actor needing any special standing is the victim (the exchange's creator), who is otherwise an ordinary account performing routine liquidity management.

### Recommendation
Add an optional slippage/deviation parameter to `ExchangeInjectContract` (and `ExchangeWithdrawContract`), analogous to the `expected` field already present on `ExchangeTransactionContract`, and enforce it in `ExchangeInjectActuator.execute`/`doValidate` by reverting if the computed `anotherTokenQuant` (or resulting ratio) deviates beyond the caller-specified bound.

### Proof of Concept
1. Account `A` creates an exchange pool via `ExchangeCreateContract` with tokens `X`/`Y` at ratio 1:2.
2. `A` broadcasts `ExchangeInjectContract` to add more `X`, expecting to receive back the paired `Y` amount at the current 1:2 ratio, per `ExchangeInjectActuator.execute` lines 71-83.
3. Attacker `B` observes `A`'s pending transaction and, in the same slot/window, submits an `ExchangeTransactionContract` swap that shifts reserves to e.g. 1:4 before `A`'s inject executes.
4. `A`'s inject now computes `anotherTokenQuant` off the skewed 1:4 ratio (per lines 73-74/79-80), locking in a bad deposit.
5. `B` submits a second `ExchangeTransactionContract` reversing the initial swap, capturing the arbitrage profit created by `A`'s mis-priced deposit. [4](#0-3) [5](#0-4)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L60-83)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
      long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
      long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

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
```

**File:** protocol/src/main/protos/api/api.proto (L1-1)
```text
syntax = "proto3";
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L57-75)
```java
      ExchangeCapsule exchangeCapsule = Commons
          .getExchangeStoreFinal(dynamicStore, exchangeStore, exchangeV2Store)
          .get(ByteArray.fromLong(exchangeTransactionContract.getExchangeId()));

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
```
