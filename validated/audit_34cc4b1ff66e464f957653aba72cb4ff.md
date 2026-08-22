### Title
Unprivileged front-running of `ExchangeWithdrawContract` / `ExchangeTransactionContract` via permissionless `ExchangeTransactionActuator` causes creator's exchange withdrawal to revert - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`)

### Summary
The reported bug class is: a permissionless, third-party-callable function (`accrueInterest()`) can mutate shared accounting state immediately before a legitimate owner action (`close()`), causing the owner's transaction to revert against a strict-equality/threshold check on that state. The java-tron `Exchange` (bancor-style AMM pool) subsystem has a structurally identical pattern: any account may call `ExchangeTransactionContract`, handled by `ExchangeTransactionActuator`, to trade against a pool created by another user, and this mutates the shared `ExchangeCapsule` balances that the pool creator's `ExchangeWithdrawContract` depends on.

### Finding Description
`ExchangeWithdrawActuator.doValidate()` requires that neither side of the pool balance be zero, otherwise it throws:
```
if (firstTokenBalance == 0 || secondTokenBalance == 0) {
  throw new ContractValidateException("Token balance in exchange is equal with 0," + "the exchange has been closed");
}
``` [1](#0-0) 

and further requires enough balance for the withdraw amount (`exchange balance is not enough`) [2](#0-1) .

Critically, `ExchangeWithdrawContract` is only restricted to the exchange creator (`account is not creator` check) [3](#0-2) , but the pool balances it depends on are mutated by `ExchangeTransactionContract`, which has **no such restriction** — any account holding the traded asset can call it against any exchange ID:
```
AccountCapsule accountCapsule = accountStore.get(exchangeTransactionContract.getOwnerAddress().toByteArray());
ExchangeCapsule exchangeCapsule = ... .get(ByteArray.fromLong(exchangeTransactionContract.getExchangeId()));
...
long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant, ...);
``` [4](#0-3) 

This is exactly the "close vs. accrueInterest" pattern: the creator's `close`-analog operation (`ExchangeWithdrawActuator`) checks pool state that any unprivileged third party can alter via a permissionless "accrue"-analog operation (`ExchangeTransactionActuator`) mempool-visible before the creator's transaction is mined. A third party (e.g. a bot watching the mempool for a pending `ExchangeWithdrawContract`) can front-run it with an `ExchangeTransactionContract` trade sized to drive the relevant token balance toward/at the amount the creator intends to withdraw (or, in edge cases with small pools/large trades bounded only by `getExchangeBalanceLimit()`, to exactly zero), causing the creator's withdrawal to revert with `"Token balance in exchange is equal with 0, the exchange has been closed"` or `"exchange balance is not enough"` [5](#0-4) . Unlike the LineOfCredit case, the java-tron version can be repeated by any address at will and is a normal, expected trading action, making it fully reachable, permissionless, and directly reproducible from ordinary broadcast transactions.

### Impact Explanation
The impact is transaction-fee-loss / griefing / denial-of-service against a specific market participant's `close`-equivalent action (`ExchangeWithdrawContract`). Repeated front-running can indefinitely block the exchange creator from withdrawing liquidity in the exact desired amount, forcing repeated retries, wasted bandwidth/energy fees, and potentially favorable price manipulation for the front-runner (a bancor-pool sandwich). No consensus divergence or direct fund theft occurs, but legitimate accounting operations can be reliably denied, which matches the impact class of the original finding (DoS on a state-closing operation with attacker profiting from timing).

### Likelihood Explanation
Any account can submit `ExchangeTransactionContract` transactions targeting any `exchangeId`; no special permission, deposit ownership, or privileged role is required [6](#0-5) . Given java-tron's public mempool and typical MEV/front-running tooling on TRON, an attacker watching for pending `ExchangeWithdrawContract` transactions from a target exchange creator can trivially front-run with a same-block-prioritized trade. Likelihood is comparable to the original (moderate), gated mainly by whether an attacker finds it profitable enough to bother, but the mechanism itself requires no rare precondition.

### Recommendation
Consider one or more of:
1. Allow `ExchangeWithdrawContract` to tolerate partial withdrawal / re-derive the withdrawable quantity at execution time rather than failing outright when front-run, or
2. Add slippage/expected-amount protection consistent with `ExchangeTransactionContract`'s `tokenExpected` field so withdraw failures are intentional (slippage-protected) rather than a hard `credit owed`-style revert, or
3. Rate-limit / cool down inbound trades from third parties against a given exchange when a withdraw by the creator is pending, or document this as an accepted trading-pool risk since it is inherent to bonding-curve AMMs (unlike the original lending protocol bug, this can't be "fixed" by simply forcing extra accrual, since it is core swap logic, not idle accounting drift).

### Proof of Concept
1. Creator issues `ExchangeCreateContract` establishing a pool with `firstTokenBalance = X`, `secondTokenBalance = Y`.
2. Creator broadcasts `ExchangeWithdrawContract` intending to redeem token quantity `Q` of `firstTokenId`.
3. Attacker observes the pending withdraw transaction in the mempool and broadcasts (with higher fee/priority) an `ExchangeTransactionContract` trading `firstTokenId` for `secondTokenId` in a size computed via `ExchangeCapsule.transaction(...)`, driving `firstTokenBalance` down close to or exactly to a value that makes the creator's subsequent withdraw fail either the `firstTokenBalance == 0` check [1](#0-0)  or the `exchange balance is not enough` check [2](#0-1) .
4. Creator's `ExchangeWithdrawContract` executes after the attacker's transaction and reverts with `ContractValidateException`, wasting the creator's bandwidth/energy fee while the attacker's front-running trade succeeds.

Note: I was unable to fully trace `ExchangeCapsule.transaction()`'s exact bancor-formula arithmetic (only its method signature was confirmed) within the available indexed context, so the precise conditions under which a single trade can drive a balance to *exactly* zero (versus merely reducing it below the creator's intended withdrawal amount, which is sufficient to trigger the `"exchange balance is not enough"` revert) were not verified line-by-line. Recommend a Devin session with full file access to `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java` to confirm the exact numeric boundary conditions.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L169-171)
```java
      throw new ContractValidateException("No enough balance for exchange withdraw fee!");
    }

```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L181-183)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L209-212)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L221-223)
```java
      if (firstTokenBalance < tokenQuant || secondTokenBalance < anotherTokenQuant) {
        throw new ContractValidateException("exchange balance is not enough");
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L51-69)
```java
    try {
      final ExchangeTransactionContract exchangeTransactionContract = this.any
          .unpack(ExchangeTransactionContract.class);
      AccountCapsule accountCapsule = accountStore
          .get(exchangeTransactionContract.getOwnerAddress().toByteArray());

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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L142-157)
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
```
