### Title
Missing range validation on `ENERGY_FEE` / `EXCHANGE_CREATE_FEE` chain-parameter proposals allows governance-approved settings to brick TVM execution and asset-exchange creation - (File: actuator/src/main/java/org/tron/core/utils/ProposalUtil.java)

### Summary
`ProposalUtil.validator()` is the central bound-checking routine invoked by `ProposalCreateActuator.validate()` for every chain parameter a witness/committee proposes to change. While most `ProposalType` cases enforce explicit numeric ranges (e.g. `[0, LONG_VALUE]`, `[1,1_000]`, `{0,1}`), the cases `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` fall straight through to `break;` with **no value validation at all**, unlike sibling fee parameters (`ACCOUNT_UPGRADE_COST`, `CREATE_ACCOUNT_FEE`, `ASSET_ISSUE_FEE`, etc.) which are explicitly bounded to `[0, LONG_VALUE]` in the same switch block.

### Finding Description
`ProposalUtil.validator()` dispatches on `ProposalType` and for most fee/parameter types enforces `value < 0 || value > LONG_VALUE` bounds: [1](#0-0) 

But `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` are grouped into a no-op case that performs no bound check whatsoever: [2](#0-1) 

This `validator()` is the sole gatekeeper called by `ProposalCreateActuator.validate()` for every parameter entry in a `ProposalCreateContract`: [3](#0-2) 

Because there is no cap on `ENERGY_FEE`, a proposal (submitted via the standard `ProposalCreateContract` broadcast transaction, reachable by any witness account, and approved through the normal `ProposalApprove`/maintenance-cycle process) can set the network-wide energy price to an astronomically large `long` value (up to `Long.MAX_VALUE`). `ENERGY_FEE` is the multiplier used everywhere energy consumption is converted into TRX fees for contract execution (`VMActuator`, `ReceiptCapsule`, `TransactionUtil`, `Wallet`). Once set to an extreme value, any account attempting a smart-contract call would be charged (or would need to burn) an amount of TRX that overflows/`exceeds any realistic balance, making every contract invocation revert with insufficient-balance/overflow errors — effectively bricking TVM execution network-wide, analogous to bricking Governor/Auction operations described in the reference report. `EXCHANGE_CREATE_FEE` is unbounded similarly, allowing exchange creation to be priced out of existence (DoS on Exchange market feature), paralleling the "stalled auction" analog.

Unlike other similarly-typed sensitive parameters in the same file (`MARKET_SELL_FEE`, `MARKET_CANCEL_FEE`, `MAX_FEE_LIMIT`, `UNFREEZE_DELAY_DAYS`, `PROPOSAL_EXPIRE_TIME`, etc., all of which have explicit min/max bound checks), `ENERGY_FEE`/`EXCHANGE_CREATE_FEE` were left with no bound — a validation gap consistent with the reported bug class of "settings that can brick core protocol operations because no range bounds are enforced."

### Impact Explanation
An extreme `ENERGY_FEE` value would make every contract call on the chain economically or arithmetically impossible to pay for, denying service to all smart-contract users network-wide (a systemic DoS on TVM execution) until a subsequent proposal fixes it — and any transaction that already committed to the bad value before a fix proposal passes remains affected. This maps to the report's "Stalling the auction"/"bricking governance" impact category: a single incorrectly-approved chain parameter proposal degrades core protocol functionality (contract execution) for the entire network, not just the proposer.

### Likelihood Explanation
Low likelihood, matching the original finding's judged severity: it requires committee/witness approval through the normal chain-parameter proposal + voting flow, which is not typically adversarial, but nothing in the code prevents a misconfigured or malicious extreme value from being proposed and approved, unlike numerically-similar fee parameters that are explicitly capped.

### Recommendation
Add an explicit bound check to the `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` cases in `ProposalUtil.validator()`, mirroring the pattern used for `ACCOUNT_UPGRADE_COST`/`CREATE_ACCOUNT_FEE`/`ASSET_ISSUE_FEE` (`value < 0 || value > LONG_VALUE` or a tighter, economically sane cap), so that `ProposalCreateActuator.validate()` rejects out-of-range proposals before they can ever be voted on and applied.

### Proof of Concept
1. A witness account crafts and broadcasts a `ProposalCreateContract` transaction with `parameters = {11 /* ENERGY_FEE code */ : Long.MAX_VALUE}`.
2. `ProposalCreateActuator.validate()` iterates the parameter map and calls `validateValue(entry)` → `ProposalUtil.validator(...)`. [3](#0-2) 
3. Inside `validator()`, the `case ENERGY_FEE:` branch performs no check and simply `break`s, so the proposal passes validation and is persisted. [2](#0-1) 
4. Once the proposal accumulates enough witness approvals during the maintenance cycle, `ENERGY_FEE` is set to the extreme value in `DynamicPropertiesStore`.
5. All subsequent smart-contract transactions computed via `VMActuator`/`ReceiptCapsule` using `getEnergyFee()` require an unpayable amount of TRX, causing contract execution to fail/revert network-wide until a corrective proposal is approved (which itself takes another full maintenance cycle to take effect).

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

**File:** actuator/src/main/java/org/tron/core/actuator/ProposalCreateActuator.java (L110-116)
```java
    if (contract.getParametersMap().size() == 0) {
      throw new ContractValidateException("This proposal has no parameter.");
    }

    for (Map.Entry<Long, Long> entry : contract.getParametersMap().entrySet()) {
      validateValue(entry);
    }
```
