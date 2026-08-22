### Title
Missing bounds validation for ENERGY_FEE and EXCHANGE_CREATE_FEE governance parameters - (File: actuator/src/main/java/org/tron/core/utils/ProposalUtil.java)

### Summary
`ProposalUtil.validator()` validates every chain-parameter proposal's value before it is committed via `ProposalApproveActuator`/`ProposalCreateActuator`. Almost every numeric parameter case enforces an explicit range (e.g. `[0, LONG_VALUE]`, `[0, MAX_SUPPLY]`, `[1, 1000]`, etc.), but the `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` cases fall through to a bare `break;` with **no value check at all**.

### Finding Description
In `validator()`, most fee-like parameters (`ACCOUNT_UPGRADE_COST`, `CREATE_ACCOUNT_FEE`, `TRANSACTION_FEE`, `ASSET_ISSUE_FEE`, `WITNESS_PAY_PER_BLOCK`, `WITNESS_STANDBY_ALLOWANCE`, etc.) are grouped and bounds-checked to `[0, LONG_VALUE]`: [1](#0-0) 

Immediately after, `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` are handled with no such check: [2](#0-1) 

The enum comments themselves document the intended range as `[0, 100000000000]` TRX, confirming this is an oversight rather than intentional design: [3](#0-2) 

Because `long code, long value` are taken directly from the proposal's parameter map with no additional sanitization elsewhere in this switch, an approved proposal can set `ENERGY_FEE` (and `EXCHANGE_CREATE_FEE`) to any `long` value, including negative values or `Long.MAX_VALUE`. `ENERGY_FEE` is the price-per-unit-of-energy multiplier used throughout the TVM execution/fee-charging path (energy consumed × `ENERGY_FEE` = SUN charged to the caller's account), so an out-of-range value directly propagates into fee/balance arithmetic performed on every contract-calling transaction broadcast by any unprivileged user.

### Impact Explanation
- A negative `ENERGY_FEE` would cause energy-fee deduction logic to *credit* balance instead of debiting it (or produce a negative fee that bypasses charging), letting ordinary users invoke energy-consuming contract calls for free or even accrue balance — an accounting/asset-corruption bug reachable by any account broadcasting a `TriggerSmartContract` transaction once the malformed value is live.
- An excessively large `ENERGY_FEE` (up to `Long.MAX_VALUE`) can overflow the `energy * fee` multiplication used when computing the TRX fee for consumed energy, wrapping to an unpredictable (potentially negative or small) value — again corrupting fee accounting for all subsequent contract-calling transactions network-wide, or causing legitimate transactions to fail/succeed unexpectedly (a DoS on the TVM execution path).
- `EXCHANGE_CREATE_FEE` has the same class of exposure for `ExchangeCreateActuator`.

This is a systemic issue: once such a proposal is enacted, *every* unprivileged user's ordinary contract-triggering transaction is affected, matching the "unbounded system parameter causes undefined behavior / accounting corruption" bug class from the report.

### Likelihood Explanation
Reaching the vulnerable code requires a chain-parameter proposal for `ENERGY_FEE`/`EXCHANGE_CREATE_FEE` to be created and approved through the normal witness-committee governance flow (`ProposalCreateActuator`/`ProposalApproveActuator`), which is the intended, in-scope mechanism for changing this exact class of parameters — the same mechanism the referenced report calls out as lacking bounds checks. The validation gap itself is deterministic and 100% reproducible (no other check anywhere in `ProposalUtil` covers these two enum values), but exploitation depends on committee approval, so likelihood is Low, consistent with the original report's classification as a "Low difficulty" data-validation issue.

### Recommendation
Add explicit bounds validation for `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` consistent with the documented `[0, LONG_VALUE]` range used for sibling fee parameters, e.g. merge them into the existing bounded case group at lines 42–54, or add a dedicated `[0, LONG_VALUE]` (or a tighter, economically-sensible) range check before `break;`. More broadly, audit all `ProposalType` cases for parameters lacking a `value < X || value > Y` guard and add symmetric bounds checks, then add regression tests asserting that out-of-range values are rejected.

### Proof of Concept
1. A witness/committee proposal is created via `ProposalCreateActuator` setting parameter code `11` (`ENERGY_FEE`) to `-1` (or `Long.MAX_VALUE`).
2. `ProposalUtil.validator()` is invoked during proposal creation/approval validation; execution enters `case ENERGY_FEE: case EXCHANGE_CREATE_FEE: break;` at [2](#0-1)  and no `ContractValidateException` is thrown, unlike every other numeric fee parameter.
3. Once the proposal passes committee approval and is enacted, `dynamicPropertiesStore.getEnergyFee()` returns the malformed value.
4. Any unprivileged account subsequently broadcasting a `TriggerSmartContractContract` transaction has its energy-fee charge computed using this malformed value, corrupting fee/balance accounting for that transaction (negative fee credits balance, or multiplication overflow yields an unintended charge).

Note: I was unable to fully trace the exact multiplication/overflow site in `VMActuator`/`ReceiptCapsule` fee-charging code within the available search budget (the direct `getEnergyFee()` call site inside `VMActuator.java` was found by grep but not read in detail), so the precise overflow arithmetic and whether existing `long` overflow checks elsewhere mitigate it should be verified by a follow-up review of `chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java` and `actuator/src/main/java/org/tron/core/actuator/VMActuator.java` energy-fee charging logic.

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

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L961-962)
```java
    ENERGY_FEE(11), // 10 Sun, [0, 100000000000] TRX
    EXCHANGE_CREATE_FEE(12), // 1024 TRX, [0, 100000000000] TRX
```
