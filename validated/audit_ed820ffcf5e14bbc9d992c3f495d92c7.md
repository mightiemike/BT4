I have enough evidence to confirm the analog vulnerability.

### Title
`ProposalUtil.validator` performs no range validation for `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` chain parameters despite documented value ranges - (File: actuator/src/main/java/org/tron/core/utils/ProposalUtil.java)

### Summary
`ProposalUtil.validator` is the central gate that checks a witness-submitted proposal's `(code, value)` pair before it is allowed to be stored and later applied to `DynamicPropertiesStore`. For almost every `ProposalType`, the switch branch enforces an explicit min/max bound matching the range documented in the `ProposalType` enum comments. However, for `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` the branch is empty and unconditionally falls through with no check at all, even though the enum itself documents an expected range of `[0, 100000000000] TRX` for both.

### Finding Description
The `validator` method's switch statement is the sole place where proposal values are checked before being accepted: [1](#0-0) 

Compare this to the enum's declared intent: [2](#0-1) 

Every neighboring case (`ACCOUNT_UPGRADE_COST`, `CREATE_ACCOUNT_FEE`, `TRANSACTION_FEE`, etc.) enforces `value < 0 || value > LONG_VALUE` checks: [3](#0-2) 

This is the same bug class as the referenced GMX report: a validation routine that is documented/expected to enforce a bound on the input value, but whose implementation simply `break`s without performing the check. In java-tron's case, this means a proposal setting `ENERGY_FEE` (proposal code 11) or `EXCHANGE_CREATE_FEE` (code 12) to any `long` value — including negative values or values far beyond any sane bound — passes validation in `ProposalCreateActuator`/`ProposalApproveActuator` (which call `ProposalUtil.validator`) and, once approved by witnesses via `ProposalController.processProposal` → `ProposalService.process`, is written directly into `DynamicPropertiesStore` as the live `ENERGY_FEE`.

The `ENERGY_FEE` value is later read in `ReceiptCapsule.payEnergyBill` and used directly to compute the energy fee an account is charged: [4](#0-3) 

A negative `sunPerEnergy` value would only be excluded by the `if (dynamicEnergyFee > 0)` guard at line 290, but the guard does not prevent an absurdly large positive value (e.g. `Long.MAX_VALUE`) from being set, which can cause `energyFee` overflow/wraparound in `(usage - accountEnergyLeft) * sunPerEnergy`, potentially producing a negative or otherwise incorrect `energyFee`, allowing accounts to bypass proper energy billing or corrupting the TRX burn/transaction-fee-pool/black-hole accounting.

### Impact Explanation
Because `ENERGY_FEE` directly drives energy cost accounting on every transaction that exceeds free energy (`ReceiptCapsule.payEnergyBill`), an unchecked/out-of-range value can corrupt resource billing chain-wide once a proposal is approved — either starving the black-hole/fee-pool of proper fees, or causing arithmetic overflow in fee computation. This affects resource and reward accounting integrity for the whole network, not just a single account.

### Likelihood Explanation
Exploitation requires a proposal to be approved by a majority of active witnesses (`ProposalController.processProposal` checks `hasMostApprovals`), which is a privileged/governance-gated action rather than an anonymous RPC path. This significantly limits likelihood compared to a purely permissionless bug, but the missing check is a genuine implementation defect: the validator silently omits enforcement that is documented as required and enforced for every structurally similar fee parameter, so a single malicious/compromised witness majority (or an accidental proposal with a typo'd value) can push the chain into an unrecoverable/incorrect energy-fee state with no on-chain safety net.

### Recommendation
Add explicit bound checks for `ENERGY_FEE` and `EXCHANGE_CREATE_FEE` in `ProposalUtil.validator`, mirroring the pattern already used for `ACCOUNT_UPGRADE_COST`/`CREATE_ACCOUNT_FEE` (e.g., `if (value < 0 || value > LONG_VALUE) throw new ContractValidateException(...)`), consistent with the range documented in the `ProposalType` enum comment.

### Proof of Concept
1. A witness (or coalition holding majority approval) submits a `ProposalCreateContract` with `parameters = {11: -1}` (or `{11: Long.MAX_VALUE}`) targeting `ENERGY_FEE`.
2. `ProposalCreateActuator.validate()` calls `ProposalUtil.validator(...)`, which hits the empty `case ENERGY_FEE: break;` branch at `ProposalUtil.java:74-76` and returns without error.
3. Once the proposal accumulates enough witness approvals, `ProposalController.processProposal` → `setDynamicParameters` → `ProposalService.process` writes the unchecked value into `DynamicPropertiesStore`'s `ENERGY_FEE`.
4. On the next transaction that exhausts an account's free energy, `ReceiptCapsule.payEnergyBill` reads this value via `dynamicPropertiesStore.getEnergyFee()` and uses it in `energyFee = (usage - accountEnergyLeft) * sunPerEnergy`, producing an overflowed/incorrect fee that is deducted from account balance and forwarded to the fee pool/black hole, corrupting network-wide resource accounting.

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

**File:** chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java (L288-302)
```java
      long sunPerEnergy = Constant.SUN_PER_ENERGY;
      long dynamicEnergyFee = dynamicPropertiesStore.getEnergyFee();
      if (dynamicEnergyFee > 0) {
        sunPerEnergy = dynamicEnergyFee;
      }
      long energyFee =
          (usage - accountEnergyLeft) * sunPerEnergy;
      this.setEnergyUsage(accountEnergyLeft);
      this.setEnergyFee(energyFee);
      long balance = account.getBalance();
      if (balance < energyFee) {
        throw new BalanceInsufficientException(
            StringUtil.createReadableString(account.createDbKey()) + " insufficient balance");
      }
      account.setBalance(balance - energyFee);
```
