### Title
Unchecked return value of `addAssetAmountV2` in `TransferAssetActuator.execute` can silently burn TRC10 tokens while marking the transaction as successful - (File: actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java)

### Summary
`TransferAssetActuator.execute` calls both `AccountCapsule.reduceAssetAmountV2` (on the sender) and `AccountCapsule.addAssetAmountV2` (on the recipient) to move a TRC10 asset balance. The sender-side call's boolean return value is checked and an exception is thrown on failure, but the recipient-side call's return value is discarded. If `addAssetAmountV2` fails (e.g. due to an overflow guard on the recipient's existing balance), the debit from the sender is already persisted while the credit to the recipient never happens, and the actuator still reports `code.SUCESS`. This mirrors the reported Solidity `DssChangeRatesSpell.cast()` pattern: a state-mutating call's failure return value is ignored, so the caller/state observes "success" even though the intended effect did not occur.

### Finding Description
In `TransferAssetActuator.execute`, the debit call is properly guarded: [1](#0-0) 

but the credit call directly below it ignores the boolean result: [2](#0-1) 

By contrast, the codebase's own helper `Commons.adjustAssetBalanceV2` demonstrates that both directions of this API are expected to be check-guarded, since `addAssetAmountV2`/`reduceAssetAmountV2` are designed to return `false` on failure (e.g., overflow) and the helper throws `BalanceInsufficientException` in either case: [3](#0-2) 

This confirms `addAssetAmountV2` has a documented failure mode (a boolean return), and that failure mode is deliberately handled elsewhere in the codebase but not in `TransferAssetActuator`. After the unchecked call, execution falls through to `ret.setStatus(fee, code.SUCESS)` and returns `true`, i.e. the transaction is committed as successful regardless of whether the credit actually took effect: [4](#0-3) 

### Impact Explanation
If `addAssetAmountV2` returns `false` (its only reason to do so, based on the analogous check in `Commons.adjustAssetBalanceV2`, is an overflow/limit condition on the destination balance), the sender's asset balance has already been reduced and persisted (`accountStore.put(ownerAddress, ...)` at line 80) before the credit is attempted. The subsequent silent failure of the credit means the transferred amount is permanently lost from total circulating balance as tracked by account balances, while the transaction result is recorded as `SUCESS`. This is a concrete accounting/state divergence: the sum of all TRC10 balances for that asset no longer equals its total issued supply, and the sender/API/UI is told the transfer succeeded even though the recipient never received the funds.

### Likelihood Explanation
This code path is reachable by any unprivileged user submitting a standard `TransferAssetContract` transaction (`TransferAssetActuator`) with a target address whose asset balance is already extremely close to the overflow boundary used internally by `addAssetAmountV2`/`Maths.addExact`. Triggering it requires crafting or waiting for a recipient account whose asset amount is near `Long.MAX_VALUE`, which is a rare but not impossible on-chain condition (e.g., an account that repeatedly accumulates a high-supply TRC10 token). The validate-phase check for the same scenario (`addExact(assetBalance, amount)` at validate time) does provide some protection by rejecting overflow before execute is reached in the common path, which lowers day-to-day likelihood, but the execute-phase call to `addAssetAmountV2` is still unguarded and is the only enforcement point if validate/execute state diverges (e.g., re-execution, race with other actuators modifying the same account's asset balance between validate and execute, TRC10 supply-adjustment operations, etc.).

### Recommendation
Check the return value of `toAccountCapsule.addAssetAmountV2(...)` in `TransferAssetActuator.execute`, mirroring the existing check on `reduceAssetAmountV2`, and throw `ContractExeException` (and avoid persisting the sender's already-reduced balance, or roll it back) if the credit fails, so the transaction cannot be marked `SUCESS` while the asset transfer did not complete. As a broader remediation, audit all other call sites of `addAssetAmountV2`/`addAssetAmount`/similar boolean-returning balance-mutation APIs in the actuator package for the same unchecked-return-value pattern.

### Proof of Concept
1. Identify or construct a recipient account `B` whose TRC10 asset balance for asset `X` is at or near `Long.MAX_VALUE - amount` (this may require an attacker-controlled account that has aggregated a large balance over time, or interaction with `AssetIssueContract`/other transfer paths that can push a balance to this boundary).
2. Attacker/sender `A`, holding balance `amount` of asset `X`, submits a `TransferAssetContract` from `A` to `B` for `amount`.
3. During `validate()`, if the overflow check does not reject the transaction (e.g. due to a race condition or a slightly different overflow boundary calculation than the one inside `addAssetAmountV2`), execution proceeds to `TransferAssetActuator.execute`.
4. `ownerAccountCapsule.reduceAssetAmountV2(...)` succeeds and is persisted (`accountStore.put(ownerAddress, ownerAccountCapsule)`).
5. `toAccountCapsule.addAssetAmountV2(...)` returns `false` internally (overflow), but the return value is discarded; `toAccountCapsule` is persisted unchanged, and `code.SUCESS` is set.
6. Result: `amount` units of asset `X` are deducted from `A` and never credited to `B` or anyone else — the tokens are unrecoverably lost from the total balance ledger, yet the transaction is recorded as successful.

Note: I was not able to directly inspect the body of `AccountCapsule.addAssetAmountV2` (its exact overflow boundary/conditions) or the precise interaction between the `validate()`-time overflow check and the `execute()`-time call within the indexed portion of the codebase, so the exact triggering condition (overflow boundary value, or whether validate/execute can diverge) could not be fully confirmed from the available index. A Devin session with full repository access would be needed to read `AccountCapsule.addAssetAmountV2`/`reduceAssetAmountV2` in full to confirm the precise overflow condition and any other failure paths.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L75-103)
```java
      AccountCapsule ownerAccountCapsule = accountStore.get(ownerAddress);
      if (!ownerAccountCapsule
          .reduceAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore)) {
        throw new ContractExeException("reduceAssetAmount failed !");
      }
      accountStore.put(ownerAddress, ownerAccountCapsule);

      toAccountCapsule
          .addAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore);
      accountStore.put(toAddress, toAccountCapsule);

      adjustBalance(accountStore, ownerAccountCapsule, -fee);
      if (dynamicStore.supportBlackHoleOptimization()) {
        dynamicStore.burnTrx(fee);
      } else {
        adjustBalance(accountStore, accountStore.getBlackhole(), fee);
      }
      ret.setStatus(fee, code.SUCESS);
    } catch (BalanceInsufficientException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    } catch (InvalidProtocolBufferException | ArithmeticException e) {
      ret.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    }

    return true;
  }
```

**File:** chainbase/src/main/java/org/tron/common/utils/Commons.java (L131-150)
```java
  public static void adjustAssetBalanceV2(AccountCapsule account, String AssetID, long amount,
      AccountStore accountStore, AssetIssueStore assetIssueStore,
      DynamicPropertiesStore dynamicPropertiesStore)
      throws BalanceInsufficientException {
    if (amount < 0) {
      if (!account.reduceAssetAmountV2(AssetID.getBytes(), -amount, dynamicPropertiesStore,
          assetIssueStore)) {
        throw new BalanceInsufficientException(
            String.format("reduceAssetAmount failed! account: %s",
                    StringUtil.encode58Check(account.createDbKey())));
      }
    } else if (amount > 0 &&
        !account.addAssetAmountV2(AssetID.getBytes(), amount, dynamicPropertiesStore,
            assetIssueStore)) {
      throw new BalanceInsufficientException(
          String.format("addAssetAmount failed! account: %s",
                  StringUtil.encode58Check(account.createDbKey())));
    }
    accountStore.put(account.getAddress().toByteArray(), account);
  }
```
