### Title
ENERGY_FEE and EXCHANGE_CREATE_FEE chain parameters can be re-parameterized via witness proposal with no sanity/bounds checks - ([File: actuator/src/main/java/org/tron/core/utils/ProposalUtil.java])

### Summary
`ProposalUtil.validator` validates almost every proposal parameter with explicit bounds (e.g. `LONG_VALUE` range checks, `MAX_SUPPLY` checks, fork-gating, `!= 1` checks, etc.), but `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` are explicitly excluded from any validation and simply `break` with no check on the proposed `value` whatsoever [1](#0-0) . This mirrors the reported bug class exactly: critical market/economic parameters (here, the TRON energy-fee, i.e. the price of 1 unit of energy in SUN, and the TRC10 exchange-pair creation fee) can be re-parameterized by a passing proposal without any invariant/threshold/limit check, allowing witnesses with sufficient voting power to push these values to economically or operationally damaging extremes.

### Finding Description
Chain parameters are changed through the witness proposal mechanism: a proposal is created (`ProposalCreateContract`), voted on by active witnesses, and once `hasMostApprovals` is satisfied, `ProposalController.processProposal` calls `setDynamicParameters` → `ProposalService.process`, which iterates the parameter map and writes the values directly into `DynamicPropertiesStore` [2](#0-1) . The `ENERGY_FEE` case in `ProposalService.process` persists the value unconditionally via `saveEnergyFee` and appends it to the energy price history used for retroactive fee calculation [3](#0-2) .

Before a proposal reaches this stage, `ProposalCreateActuator` calls `ProposalUtil.validator` to reject "bad" parameter values. However, for `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` the switch statement contains only:
```java
case ENERGY_FEE:
case EXCHANGE_CREATE_FEE:
  break;
```
with no range, non-negativity, or sanity check on `value` [1](#0-0) . Contrast this with virtually every other numeric parameter in the same method, all of which enforce an explicit `[0, LONG_VALUE]` or `[0, MAX_SUPPLY]` bound, e.g. `ACCOUNT_UPGRADE_COST`/`CREATE_ACCOUNT_FEE`/`TRANSACTION_FEE` [4](#0-3) , `MEMO_FEE` [5](#0-4) , `MARKET_SELL_FEE`/`MARKET_CANCEL_FEE` [6](#0-5) , and `MAX_FEE_LIMIT` [7](#0-6) .

`ENERGY_FEE` is the SUN-per-energy conversion rate consumed throughout TVM execution accounting (`VMActuator`, `ReceiptCapsule`, `Wallet.getEnergyFee`, and the JSON-RPC layer `TronJsonRpcImpl`) — it directly governs how much TRX every smart-contract call burns for the energy it consumes. Because the proposal-value type is a raw `long` and no bound is enforced, a passing proposal can set `ENERGY_FEE` to `0` (making all contract execution free of energy cost, enabling unbounded computational/resource-exhaustion spam against the network) or to an extremely large value (making ordinary contract calls prohibitively/uneconomically expensive, effectively a network-wide DoS on TVM usage), or theoretically even a value that could produce accounting overflow when multiplied against energy usage in fee computations. The same absence of bound applies to `EXCHANGE_CREATE_FEE`, the fee for creating TRC10 bancor-style exchange pairs, which could be set to `0` (enabling free flooding of the exchange market) or to an unreasonably high value (freezing out legitimate participants), directly analogous to the "manipulate SPREAD/PRICE_FLOOR to control the buy curve" scenario in the report.

### Impact Explanation
An accepted proposal that sets `ENERGY_FEE` to `0` or to an extreme value changes the economics of every contract invocation network-wide, either allowing computational-resource DoS (free energy) or pricing out legitimate users (excessive fee) — this is a protocol-level economic/DoS impact reachable purely through the standard, unprivileged witness-proposal RPC/broadcast-transaction flow (`ProposalCreateContract`), with no additional bound enforced at approval time in `ProposalService`. `EXCHANGE_CREATE_FEE` similarly controls TRC10 bancor-exchange market entry economics.

### Likelihood Explanation
Exploitation requires proposal approval by a super-majority of active witnesses (`hasMostApprovals`) [8](#0-7) , which is the same threshold used for all other legitimate parameter changes; unlike other parameters, however, there is zero automated defense-in-depth (input sanity check) once that threshold is met for `ENERGY_FEE`/`EXCHANGE_CREATE_FEE`, whereas nearly every other economically sensitive parameter is explicitly bounded. This means a single mistaken or malicious proposal (accidental fat-fingered value, or coordinated colluding witnesses) can silently pass without the `ProposalUtil.validator` rejecting an obviously unreasonable value, unlike for `TRANSACTION_FEE`, `MEMO_FEE`, `MARKET_SELL_FEE`, etc.

### Recommendation
Add explicit range validation for `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` in `ProposalUtil.validator`, consistent with the pattern already used for other fee parameters (e.g. bound to `[1, LONG_VALUE]` or a similarly reasoned range, disallowing `0` if free execution is undesired), mirroring the checks already present for `TRANSACTION_FEE`, `MEMO_FEE`, `MARKET_SELL_FEE`, and `MARKET_CANCEL_FEE`. Additionally, review whether hard step-limits (max relative increase/decrease per proposal) should be added for `ENERGY_FEE` given its network-wide economic significance.

### Proof of Concept
1. Craft a `ProposalCreateContract` transaction with `parameters = {ENERGY_FEE_code: 0}` (or an extremely large `long` value) and broadcast it as any account (proposal creation itself requires no special privilege beyond being a valid account, subject only to whatever fee/permission the proposal-create actuator requires).
2. `ProposalCreateActuator` invokes `ProposalUtil.validator`, which for `ENERGY_FEE` executes only `break;` — the value `0` (or the extreme value) passes validation unmodified [1](#0-0) .
3. Once witnesses vote and the proposal reaches `hasMostApprovals`, `ProposalService.process` writes the unchecked value directly into `DynamicPropertiesStore` via `saveEnergyFee` [3](#0-2) .
4. All subsequent TVM executions network-wide now use the corrupted `ENERGY_FEE`, resulting in free (or economically impossible) energy consumption for every contract call.

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

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L370-396)
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
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L398-414)
```java
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
        break;
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L587-596)
```java
      case MEMO_FEE: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_6)) {
          throw new ContractValidateException(
              "Bad chain parameter id [MEMO_FEE]");
        }
        if (value < 0 || value > 1_000_000_000) {
          throw new ContractValidateException(
              "This value[MEMO_FEE] is only allowed to be in the range 0-1000_000_000");
        }
        break;
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

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L83-90)
```java
        case ENERGY_FEE: {
          manager.getDynamicPropertiesStore().saveEnergyFee(entry.getValue());
          // update energy price history
          manager.getDynamicPropertiesStore().saveEnergyPriceHistory(
              manager.getDynamicPropertiesStore().getEnergyPriceHistory()
                  + "," + proposalCapsule.getExpirationTime() + ":" + entry.getValue());
          break;
        }
```
