### Title
Unprotected Ratio-Dependent Revert in `ExchangeInjectActuator` Causes DoS on Legitimate Injections - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java)

### Summary
`ExchangeInjectActuator::doValidate()` computes a derived "another token quantity" from the current pool ratio and reverts the entire transaction if that *calculated* value is `<= 0`, exactly mirroring the reported bug class: a threshold check on an internally-derived amount (not the value the caller directly chose) that can be pushed below the minimum by state the caller does not control, causing an otherwise correctly funded operation to revert.

### Finding Description
In `ExchangeInjectActuator`, the caller only specifies `tokenQuant` for one side of the pair. The actuator then derives the other side's quantity from the *current* exchange balances: [1](#0-0) 

`anotherTokenQuant` is a function of `firstTokenBalance`/`secondTokenBalance`, which are live, shared, mutable state of the `ExchangeCapsule` — any other account's `ExchangeInjectContract`, `ExchangeTransactionContract`, or `ExchangeWithdrawContract` executed in an earlier transaction of the same block (or an earlier block) shifts this ratio. Unlike `ExchangeTransactionActuator`, which exposes a `tokenExpected` slippage-protection parameter the caller can set: [2](#0-1) 

`ExchangeInjectActuator` has no equivalent parameter. The caller cannot express "only proceed if the derived side is at least X"; the actuator itself hard-fails when the derived quantity rounds down to `<= 0`: [3](#0-2) 

This is structurally identical to the Sherlock report: `RsETHAdapter::_stake()` reverts when a value calculated from shared/mutable protocol state (`prefundedDeposit`'s buffer math) drops below `RSETH_DEPOSIT_POOL.minAmountToDeposit()`, even though the caller supplied a "correct" amount. Here, `ExchangeInjectActuator` reverts when a value calculated from shared/mutable exchange-pool state drops to `0`, even though the caller supplied a valid, funded `tokenQuant`.

### Impact Explanation
Any account attempting to inject liquidity into a low-ratio or thinly-quantized exchange pair can have its transaction unconditionally reverted purely because other, unrelated transactions altered the pool ratio between transaction construction and execution — the caller pays the transaction bandwidth/fee cost with a guaranteed failure and cannot express a minimum-acceptable-output guard as `ExchangeTransactionActuator` callers can. This is a state/accounting-availability issue confined to the `ExchangeInjectContract` code path: legitimate, correctly funded exchange-injection operations can be forced to fail (griefed) by ordinary trading activity on the same pair, with no recourse for the caller to avoid it besides guessing a larger `tokenQuant`.

### Likelihood Explanation
Likelihood is moderate: it requires (a) an exchange pair with an extreme or low-precision ratio (common for small/legacy TRC10 pairs created early in an exchange's life, or after large withdrawals), and (b) intervening transactions on the same pair within the same block window. No privileged role is needed — any unprivileged user's `ExchangeInjectContract` can trigger or be victim of this, and any other unprivileged user's trade against the same pair can be the proximate cause.

### Recommendation
Add an explicit minimum-acceptable-output parameter to `ExchangeInjectContract` (mirroring `ExchangeTransactionContract.tokenExpected`), so callers can bound acceptable slippage instead of only being told post-hoc that "the calculated token quant must be greater than 0." Alternatively, when the computed `anotherTokenQuant` would be `<= 0`, consider computing the minimum viable `tokenQuant` and either rejecting earlier with actionable guidance or clamping/rounding in the caller's favor rather than allowing an unbounded, unpredictable failure surface driven purely by third-party pool activity.

### Proof of Concept
1. Exchange pool exists with `firstTokenBalance = 100`, `secondTokenBalance = 1` (extreme ratio, achievable via prior withdrawals/trades).
2. Alice submits `ExchangeInjectContract` with `tokenQuant = 1` for `firstTokenId`, expecting `anotherTokenQuant = secondTokenBalance * tokenQuant / firstTokenBalance = 1*1/100 = 0` (floor division).
3. Existing test coverage confirms this exact failure mode already occurs deterministically for boundary ratios: [4](#0-3) 
4. Because the ratio is mutable pool state (not fixed at pool creation, unlike `ParticipateAssetIssueActuator`'s `num`/`trxNum`), an attacker/other trader can shift `firstTokenBalance`/`secondTokenBalance` via a preceding `ExchangeTransactionContract` in the same block to deliberately push a pending, previously-valid `ExchangeInjectContract` into this same `anotherTokenQuant <= 0` revert path, at the fee cost of the victim and no cost-effective defense available to the victim (no slippage parameter exists on this contract type).

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeInjectActuatorTest.java (L1179-1216)
```java
  public void SameTokenNameCloseCalculatedTokenQuantLessThanZero() {
    dbManager.getDynamicPropertiesStore().saveAllowSameTokenName(0);
    InitExchangeBeforeSameTokenNameActive();
    long exchangeId = 2;
    String firstTokenId = "_";
    long firstTokenQuant = 100L;
    String secondTokenId = "def";
    long secondTokenQuant = 400000000L;

    byte[] ownerAddress = ByteArray.fromHexString(OWNER_ADDRESS_FIRST);
    AccountCapsule accountCapsule = dbManager.getAccountStore().get(ownerAddress);
    accountCapsule.addAssetAmount(secondTokenId.getBytes(), secondTokenQuant, true);
    accountCapsule.setBalance(firstTokenQuant);
    dbManager.getAccountStore().put(ownerAddress, accountCapsule);

    ExchangeInjectActuator actuator = new ExchangeInjectActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract(
        OWNER_ADDRESS_FIRST, exchangeId, firstTokenId, firstTokenQuant));

    TransactionResultCapsule ret = new TransactionResultCapsule();

    try {
      actuator.validate();
      actuator.execute(ret);
      fail();
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("the calculated token quant  must be greater than 0",
          e.getMessage());
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    } finally {
      dbManager.getExchangeStore().delete(ByteArray.fromLong(1L));
      dbManager.getExchangeStore().delete(ByteArray.fromLong(2L));
      dbManager.getExchangeV2Store().delete(ByteArray.fromLong(1L));
      dbManager.getExchangeV2Store().delete(ByteArray.fromLong(2L));
    }
  }
```
