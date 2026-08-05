### Title
Missing upper bound on ENERGY_FEE / EXCHANGE_CREATE_FEE committee parameters allows unbounded fee to be set on unprivileged users - (File: actuator/src/main/java/org/tron/core/utils/ProposalUtil.java)

### Summary
The `ProposalUtil.validator` method, which validates every committee-proposed chain parameter change before it is applied by `ProposalService.process`, enforces a `[0, LONG_VALUE]` bound for most fee-type parameters (`ACCOUNT_UPGRADE_COST`, `CREATE_ACCOUNT_FEE`, `TRANSACTION_FEE`, `ASSET_ISSUE_FEE`, `WITNESS_PAY_PER_BLOCK`, `WITNESS_STANDBY_ALLOWANCE`, `CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT`, `CREATE_NEW_ACCOUNT_BANDWIDTH_RATE`), but the `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` cases fall through with a bare `break;` and perform no bounds check at all. [1](#0-0) [2](#0-1) 

This mirrors the reported Bridge.sol issue: a fee-setter with `newFee` accepted without any sanity/upper-bound check, allowing the fee to be pushed to an arbitrarily large (or `Long.MAX_VALUE`-adjacent) value.

### Finding Description
`ENERGY_FEE` sets the price (in SUN) charged per unit of energy consumed by every TVM contract call on the network, and `EXCHANGE_CREATE_FEE` sets the fixed TRX fee charged to any user creating a TRC10 exchange pair. Both values are propagated unchecked into `DynamicPropertiesStore` by `ProposalService.process`: [3](#0-2) 

and stored via `saveEnergyFee`/`saveExchangeCreateFee`, later read back by unprivileged-user-facing code paths such as `Wallet.getEnergyFee()` and `ExchangeCreateActuator.calcFee()`, which is `dynamicStore.getExchangeCreateFee()` and directly debited from any ordinary account balance in `ExchangeCreateActuator.execute()`: [4](#0-3) 

Every other similarly-shaped monetary/rate parameter in the same switch statement is explicitly capped (e.g. `MAX_FEE_LIMIT`, `MARKET_SELL_FEE`, `MARKET_CANCEL_FEE`, and the generic `LONG_VALUE`-bounded group), confirming this is an inconsistency/omission rather than an intentional design choice: [5](#0-4) 

Because `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` skip the bound check entirely, a proposal that sets either to an extreme value (up to `Long.MAX_VALUE`) will pass validation in `ProposalUtil.validator` and be committed once approved.

### Impact Explanation
Once an unbounded `ENERGY_FEE` is committed, every unprivileged user's smart-contract transactions become subject to an arbitrarily large per-unit-energy charge, which can drain account balances disproportionately to actual resource consumption, effectively act as a denial-of-service against normal contract execution network-wide, and interacts with fee/energy accounting (`Wallet.getEnergyFee`, `ReceiptCapsule`, `TransactionUtil`) that multiplies energy usage by this fee — creating overflow/economic risk in downstream fee computations. Similarly, an unbounded `EXCHANGE_CREATE_FEE` can be weaponized to make `ExchangeCreateActuator.execute()` charge a fee far exceeding any reasonable value, again unfairly burdening unprivileged accounts that submit `ExchangeCreateContract` transactions. This matches the report's core concern: improper upper-bound definition on a fee leading to excessive fees, user financial harm, and centralization risk.

### Likelihood Explanation
Changing these parameters requires committee/witness-proposal governance (majority-approved `ProposalApproveContract`/proposal creation flow), so the trigger itself is a trusted-role action, not something any arbitrary unprivileged user can invoke directly. However, unlike the Bridge.sol case (a single `onlyAdmin` party), the java-tron committee mechanism can be influenced by a coalition of witnesses reaching majority approval, and — critically — the missing bound means the code itself provides no safety net against an operator mistake or malicious/compromised majority. The concrete impact (excessive fee applied) always lands on unprivileged users, matching the report's "impact" framing even though the write path requires witness consensus.

### Recommendation
Add explicit range validation for `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` in `ProposalUtil.validator`, consistent with sibling cases, e.g.:
```java
case ENERGY_FEE:
case EXCHANGE_CREATE_FEE: {
  if (value < 0 || value > LONG_VALUE) {
    throw new ContractValidateException(LONG_VALUE_ERROR);
  }
  break;
}
```
or a tighter, purpose-specific bound analogous to `MARKET_SELL_FEE`/`MARKET_CANCEL_FEE` (`[0, 10_000_000_000L]`).

### Proof of Concept
1. A committee proposal is created with `parameters = {ENERGY_FEE.getCode(): Long.MAX_VALUE}` (or any excessively large value) via the standard `ProposalCreateContract` flow.
2. `ProposalUtil.validator` reaches `case ENERGY_FEE: break;` at [2](#0-1) 
without throwing, so the proposal passes validation.
3. Once approved by witnesses, `ProposalService.process` calls `manager.getDynamicPropertiesStore().saveEnergyFee(entry.getValue())`: [6](#0-5) 
4. All subsequent smart-contract transactions from unprivileged users are now charged energy at this unbounded rate, and `ExchangeCreateActuator` (if `EXCHANGE_CREATE_FEE` were similarly abused) will subtract the excessive fee directly from any user's account balance: [4](#0-3) 

**Uncertainty note:** I could not fully trace every downstream multiplication site where `getEnergyFee()` is multiplied by energy usage (to confirm a specific overflow), because the index's search results for `OperationActions.java`/`VMActuator.java` usage of `getEnergyFee()` did not return matching line content within the available tool calls. The core root-cause claim (missing bound in `ProposalUtil.validator` for `ENERGY_FEE`/`EXCHANGE_CREATE_FEE`) is confirmed directly from source, but the exact numeric overflow/economic-impact scenario at the fee-multiplication sites would benefit from further review in a full Devin session with complete file access.

### Citations

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L42-54)
```java
      case ACCOUNT_UPGRADE_COST:
      case CREATE_ACCOUNT_FEE:
      case TRANSACTION_FEE:
      case ASSET_ISSUE_FEE:
      case WITNESS_PAY_PER_BLOCK:
      case WITNESS_STANDBY_ALLOWANCE:
      case CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT:
      case CREATE_NEW_ACCOUNT_BANDWIDTH_RATE: {
        if (value < 0 || value > LONG_VALUE) {
          throw new ContractValidateException(LONG_VALUE_ERROR);
        }
        break;
      }
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L74-76)
```java
      case ENERGY_FEE:
      case EXCHANGE_CREATE_FEE:
        break;
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L370-413)
```java
      case MARKET_SELL_FEE: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_1)) {
          throw new ContractValidateException("Bad chain parameter id [MARKET_SELL_FEE]");
        }
        if (!dynamicPropertiesStore.supportAllowMarketTransaction()) {
          throw new ContractValidateException(
              "Market Transaction is not activated, can not set Market Sell Fee");
        }
        if (value < 0 || value > 10_000_000_000L) {
          throw new ContractValidateException(
              "Bad MARKET_SELL_FEE parameter value, valid range is [0,10_000_000_000L]");
        }
        break;
      }
      case MARKET_CANCEL_FEE: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_1)) {
          throw new ContractValidateException("Bad chain parameter id [MARKET_CANCEL_FEE]");
        }
        if (!dynamicPropertiesStore.supportAllowMarketTransaction()) {
          throw new ContractValidateException(
              "Market Transaction is not activated, can not set Market Cancel Fee");
        }
        if (value < 0 || value > 10_000_000_000L) {
          throw new ContractValidateException(
              "Bad MARKET_CANCEL_FEE parameter value, valid range is [0,10_000_000_000L]");
        }
        break;
      }
      case MAX_FEE_LIMIT: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_1_2)) {
          throw new ContractValidateException("Bad chain parameter id [MAX_FEE_LIMIT]");
        }
        if (value < 0) {
          throw new ContractValidateException(
              "Bad MAX_FEE_LIMIT parameter value, value must not be negative");
        } else if (value > 10_000_000_000L) {
          if (dynamicPropertiesStore.getAllowTvmLondon() == 0) {
            throw new ContractValidateException(
                "Bad MAX_FEE_LIMIT parameter value, valid range is [0,10_000_000_000L]");
          }
          if (value > LONG_VALUE) {
            throw new ContractValidateException(LONG_VALUE_ERROR);
          }
        }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L83-94)
```java
        case ENERGY_FEE: {
          manager.getDynamicPropertiesStore().saveEnergyFee(entry.getValue());
          // update energy price history
          manager.getDynamicPropertiesStore().saveEnergyPriceHistory(
              manager.getDynamicPropertiesStore().getEnergyPriceHistory()
                  + "," + proposalCapsule.getExpirationTime() + ":" + entry.getValue());
          break;
        }
        case EXCHANGE_CREATE_FEE: {
          manager.getDynamicPropertiesStore().saveExchangeCreateFee(entry.getValue());
          break;
        }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L43-60)
```java
    long fee = calcFee();
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    AssetIssueStore assetIssueStore = chainBaseManager.getAssetIssueStore();
    ExchangeStore exchangeStore = chainBaseManager.getExchangeStore();
    ExchangeV2Store exchangeV2Store = chainBaseManager.getExchangeV2Store();
    try {
      final ExchangeCreateContract exchangeCreateContract = this.any
          .unpack(ExchangeCreateContract.class);
      AccountCapsule accountCapsule = accountStore
          .get(exchangeCreateContract.getOwnerAddress().toByteArray());

      byte[] firstTokenID = exchangeCreateContract.getFirstTokenId().toByteArray();
      byte[] secondTokenID = exchangeCreateContract.getSecondTokenId().toByteArray();
      long firstTokenBalance = exchangeCreateContract.getFirstTokenBalance();
      long secondTokenBalance = exchangeCreateContract.getSecondTokenBalance();

      long newBalance = subtractExact(accountCapsule.getBalance(), fee);
```
