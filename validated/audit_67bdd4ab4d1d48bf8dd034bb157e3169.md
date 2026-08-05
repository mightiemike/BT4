### Title
Missing value validation for `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` governance parameters allows unbounded/negative economic settings - (File: `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java`)

### Summary
The `ProposalUtil.validator` method enforces bounds on every governance-adjustable chain parameter except two: `ENERGY_FEE` and `EXCHANGE_CREATE_FEE`, whose `case` blocks fall straight to `break;` with no range check at all, unlike every other numeric parameter in the same switch statement.

### Finding Description
`ProposalUtil.validator` is the single gatekeeper invoked from `ProposalCreateActuator.validate()` to sanity-check every parameter value a witness committee member proposes via `ProposalCreateContract`. [1](#0-0) 

Almost every parameter case in the switch enforces an explicit min/max range (e.g. `ACCOUNT_UPGRADE_COST`, `CREATE_ACCOUNT_FEE`, `TRANSACTION_FEE`, `WITNESS_PAY_PER_BLOCK` are bounded to `[0, LONG_VALUE]`, and `MAINTENANCE_TIME_INTERVAL` is bounded to `[3*27*1000, 24*3600*1000]`), mirroring the fix pattern the external report recommends for `EmergencyProposer.setQuorum`/`setMinimumWaitTime`. [2](#0-1) 

However, `ENERGY_FEE` (id 11) and `EXCHANGE_CREATE_FEE` (id 12) are grouped together and simply `break;` without any bounds check whatsoever: [3](#0-2) 

This means a `ProposalCreateContract` with `parameters = {11: <any long>}` passes validation regardless of value — including `0`, a negative number, or `Long.MAX_VALUE`. `ENERGY_FEE` is the price (in SUN) charged per unit of energy consumed by TVM execution; it is read via `DynamicPropertiesStore.getEnergyFee()` and consumed directly in fee/energy accounting in `VMActuator.java` and elsewhere to compute the TRX cost of contract execution. [4](#0-3) 

The same root cause applies as in the `EmergencyProposer` report: a numerical governance parameter that feeds directly into economic accounting logic can be set to an unbounded or degenerate value because the validator does not enforce a sane range for it, unlike its sibling parameters.

### Impact Explanation
If `ENERGY_FEE` is approved with value `0`, TVM/contract execution becomes effectively free of energy cost in TRX terms, allowing unpriced/underpriced consumption of network computational resources (an "underpriced public work" class issue) — attackers could execute arbitrarily heavy contract calls burning real network energy/CPU while paying no fee. Conversely, an extreme value could make the network unusable for contract calls (denial of service for the TVM), or, combined with fee arithmetic elsewhere, cause overflow in fee computation paths that multiply energy-used by `ENERGY_FEE`. `EXCHANGE_CREATE_FEE` shares the same lack of validation and controls the TRX cost to create a bancor-style exchange, so it could similarly be set to `0` (free exchange creation, enabling spam) or an overflowing value.

### Likelihood Explanation
Exploitation requires a `ProposalCreateContract` to be proposed by a witness and approved by a majority of active witnesses via the normal on-chain governance flow (`ProposalApproveActuator` / `ProposalController.processProposal`), so this is not exploitable by an arbitrary unprivileged user directly, but it is a genuine gap in defense-in-depth validation that every other parameter in the same function has, and a single malicious/compromised or careless witness proposal could push the network into an incorrect economic state that is difficult to reverse without another proposal (the same "bricking" risk described in the original report for `EmergencyProposer`).

### Recommendation
Add explicit bounds to the `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` cases consistent with the pattern used for the other fee parameters (e.g. `if (value < 0 || value > LONG_VALUE) throw new ContractValidateException(...)`), matching the treatment given to `ACCOUNT_UPGRADE_COST`, `CREATE_ACCOUNT_FEE`, `TRANSACTION_FEE`, etc. [5](#0-4) 

### Proof of Concept
1. A witness account submits a `ProposalCreateContract` with `parameters = {11: 0}` (or any negative/`Long.MAX_VALUE` value) via `ProposalCreateActuator`.
2. `ProposalCreateActuator.validate()` calls `ProposalUtil.validateValue` → `ProposalUtil.validator`, which hits `case ENERGY_FEE: break;` and returns without throwing, so the malformed value passes validation. [6](#0-5) 
3. Once a majority of active witnesses approve, `ProposalController.processProposal` → `ProposalService.process` writes the new `ENERGY_FEE` into `DynamicPropertiesStore`, and all subsequent TVM executions compute their TRX energy cost using this unvalidated value. [7](#0-6)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ProposalCreateActuator.java (L114-125)
```java
    for (Map.Entry<Long, Long> entry : contract.getParametersMap().entrySet()) {
      validateValue(entry);
    }

    return true;
  }

  private void validateValue(Map.Entry<Long, Long> entry) throws ContractValidateException {
    ProposalUtil
        .validator(chainBaseManager.getDynamicPropertiesStore(), forkController, entry.getKey(),
            entry.getValue());
  }
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L35-53)
```java
      case MAINTENANCE_TIME_INTERVAL: {
        if (value < 3 * 27 * 1000 || value > 24 * 3600 * 1000) {
          throw new ContractValidateException(
              "Bad chain parameter value, valid range is [3 * 27 * 1000,24 * 3600 * 1000]");
        }
        return;
      }
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
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L74-76)
```java
      case ENERGY_FEE:
      case EXCHANGE_CREATE_FEE:
        break;
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L1-1)
```java
package org.tron.core.store;
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalController.java (L74-99)
```java
  public void processProposal(ProposalCapsule proposalCapsule) {

    List<ByteString> activeWitnesses = this.manager.getWitnessScheduleStore().getActiveWitnesses();
    if (proposalCapsule.hasMostApprovals(activeWitnesses)) {
      logger.info(
          "Processing proposal,id:{},it has received most approvals, "
              + "begin to set dynamic parameter:{}, "
              + "and set proposal state as APPROVED",
          proposalCapsule.getID(), proposalCapsule.getParameters());
      setDynamicParameters(proposalCapsule);
      proposalCapsule.setState(State.APPROVED);
      manager.getProposalStore().put(proposalCapsule.createDbKey(), proposalCapsule);
    } else {
      logger.info(
          "Processing proposal,id:{}, "
              + "it has not received enough approvals, set proposal state as DISAPPROVED",
          proposalCapsule.getID());
      proposalCapsule.setState(State.DISAPPROVED);
      manager.getProposalStore().put(proposalCapsule.createDbKey(), proposalCapsule);
    }

  }

  public void setDynamicParameters(ProposalCapsule proposalCapsule) {
    ProposalService.process(manager, proposalCapsule);
  }
```
