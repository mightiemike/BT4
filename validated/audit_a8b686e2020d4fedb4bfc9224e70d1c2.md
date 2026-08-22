## Finding

### Title
Proposal parameter invariants are validated only at proposal-creation time and never re-checked at proposal-execution time, allowing stale/invalid on-chain parameters to be committed - (File: `framework/src/main/java/org/tron/core/consensus/ProposalService.java`)

### Summary
This is a direct analog of the reported bug class: a precondition is enforced only when a request is *created* (`ProposalCreateActuator.validate()` → `ProposalUtil.validator()`), but is never re-checked when the request is actually *executed* (`ProposalController.processProposal()` → `ProposalService.process()`). Because proposals in java-tron are only executed once they expire (which can be a long time after creation, spanning many maintenance cycles), the chain state the creation-time check relied upon can change in the interim, and the stale proposal is still applied unconditionally.

### Finding Description
`ProposalCreateActuator.validate()` calls `ProposalUtil.validator()` to check each parameter before the proposal is stored: [1](#0-0) 

One example is `FORBID_TRANSFER_TO_CONTRACT`, whose creation-time validator requires `ALLOW_CREATION_OF_CONTRACTS` to currently be `1`: [2](#0-1) 

Similarly, `ALLOW_OLD_REWARD_OPT` requires `useNewRewardAlgorithm()` to currently be true at creation time: [3](#0-2) 

However, a proposal is not executed at creation time — it is only processed by `ProposalController.processProposals()` once it has expired, at which point `ProposalController.processProposal()` decides approval and calls `setDynamicParameters()` → `ProposalService.process()`: [4](#0-3) 

`ProposalService.process()` applies the stored parameters directly to `DynamicPropertiesStore` with **no re-validation** against the current chain state for most parameter types, including `FORBID_TRANSFER_TO_CONTRACT` and `ALLOW_OLD_REWARD_OPT`: [5](#0-4) [6](#0-5) 

Note that a handful of other cases (`REMOVE_THE_POWER_OF_THE_GR`, `ALLOW_MULTI_SIGN`, `ALLOW_ADAPTIVE_ENERGY`, `ALLOW_MARKET_TRANSACTION`, `ALLOW_CANCEL_ALL_UNFREEZE_V2`) do guard against re-application with an `if (current == 0)` check, showing the developers were aware some invariant re-checking is needed — but this pattern was not applied consistently to all parameters that have a creation-time precondition, e.g. `FORBID_TRANSFER_TO_CONTRACT` and `ALLOW_OLD_REWARD_OPT`.

Because a proposal remains `PENDING` and unexecuted until `getNextMaintenanceTime()` catches up to its `expirationTime` (which by design can span multiple maintenance rounds via `ProposalCreateActuator.execute()`'s rounding logic), an attacker-controlled or legitimately-created proposal that satisfied the precondition at creation time can execute later after the precondition has been reversed by an intervening proposal, silently committing an invalid/unintended state transition.

### Impact Explanation
This allows chain-parameter invariants (e.g., "contracts must be allowed before transfers-to-contract can be forbidden", or "new reward algorithm must be active before old-reward-opt can be re-enabled") to be violated, corrupting consensus-critical dynamic properties without any check at the point of actual state mutation. This is a concrete consensus/accounting-state corruption vector reachable purely through normal witness-broadcast `ProposalCreateContract` transactions and the periodic maintenance cycle — no privileged or out-of-scope actor is required beyond the witnesses who are the intended and expected callers of this feature.

### Likelihood Explanation
Requires two conflicting `ProposalCreateContract` proposals to be active concurrently across maintenance cycles (a reachable, unprivileged use of the intended proposal mechanism), so likelihood is moderate — it depends on proposal timing/ordering rather than any additional exploit primitive.

### Recommendation
Re-validate each parameter's preconditions inside `ProposalService.process()` (or immediately before it in `ProposalController.processProposal()`) against the current `DynamicPropertiesStore`/fork state at execution time, mirroring the checks already performed in `ProposalUtil.validator()`, instead of relying solely on the state that existed when the proposal was created.

### Proof of Concept
1. Witness A creates a proposal with parameter `ALLOW_CREATION_OF_CONTRACTS = 1`; it is approved and executed, setting the property to `1`.
2. Witness B creates a proposal with parameter `FORBID_TRANSFER_TO_CONTRACT = 1` while `ALLOW_CREATION_OF_CONTRACTS == 1`, passing `ProposalUtil.validator()`'s check at `ProposalUtil.java:318-322`. This proposal remains `PENDING` until it expires (potentially several maintenance cycles later).
3. Before Witness B's proposal expires, Witness C creates and gets approved a competing proposal setting `ALLOW_CREATION_OF_CONTRACTS = 0`, executed via `ProposalService.process()` case `ALLOW_CREATION_OF_CONTRACTS` at `ProposalService.java:73-76`.
4. When Witness B's proposal subsequently expires and is processed by `ProposalController.processProposal()`, `ProposalService.process()`'s `FORBID_TRANSFER_TO_CONTRACT` case (`ProposalService.java:204-207`) unconditionally sets `FORBID_TRANSFER_TO_CONTRACT = 1` with no re-check, even though the required precondition (`ALLOW_CREATION_OF_CONTRACTS == 1`) no longer holds — reproducing the reported TOCTOU class where a check enforced at creation is bypassed at execution.

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

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L309-324)
```java
      case FORBID_TRANSFER_TO_CONTRACT: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_3_6_6)) {

          throw new ContractValidateException(BAD_PARAM_ID);
        }
        if (value != 1) {
          throw new ContractValidateException(
              "This value[FORBID_TRANSFER_TO_CONTRACT] is only allowed to be 1");
        }
        if (dynamicPropertiesStore.getAllowCreationOfContracts() == 0) {
          throw new ContractValidateException(
              "[ALLOW_CREATION_OF_CONTRACTS] proposal must be approved "
                  + "before [FORBID_TRANSFER_TO_CONTRACT] can be proposed");
        }
        break;
      }
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L736-754)
```java
      case ALLOW_OLD_REWARD_OPT: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_7_4)) {
          throw new ContractValidateException(
              "Bad chain parameter id [ALLOW_OLD_REWARD_OPT]");
        }
        if (dynamicPropertiesStore.allowOldRewardOpt()) {
          throw new ContractValidateException(
              "[ALLOW_OLD_REWARD_OPT] has been valid, no need to propose again");
        }
        if (value != 1) {
          throw new ContractValidateException(
              "This value[ALLOW_OLD_REWARD_OPT] is only allowed to be 1");
        }
        if (!dynamicPropertiesStore.useNewRewardAlgorithm()) {
          throw new ContractValidateException(
              "[ALLOW_NEW_REWARD] or [ALLOW_TVM_VOTE] proposal must be approved "
                  + "before [ALLOW_OLD_REWARD_OPT] can be proposed");
        }
        break;
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalController.java (L26-99)
```java
  public void processProposals() {
    long latestProposalNum = manager.getDynamicPropertiesStore().getLatestProposalNum();
    if (latestProposalNum == 0) {
      logger.info("latestProposalNum is 0, return");
      return;
    }

    long proposalNum = latestProposalNum;

    ProposalCapsule proposalCapsule = null;

    while (proposalNum > 0) {
      try {
        proposalCapsule = manager.getProposalStore()
            .get(ProposalCapsule.calculateDbKey(proposalNum));
      } catch (Exception ex) {
        logger.error("", ex);
        proposalNum--;
        continue;
      }

      if (proposalCapsule.hasProcessed()) {
        logger
            .info("Proposal has processed, id:[{}], skip it and before it",
                proposalCapsule.getID());
        //proposals with number less than this one, have been processed before
        break;
      }

      if (proposalCapsule.hasCanceled()) {
        logger.info("Proposal has canceled, id:[{}], skip it", proposalCapsule.getID());
        proposalNum--;
        continue;
      }

      long currentTime = manager.getDynamicPropertiesStore().getNextMaintenanceTime();
      if (proposalCapsule.hasExpired(currentTime)) {
        processProposal(proposalCapsule);
        proposalNum--;
        continue;
      }

      proposalNum--;
      logger.info("Proposal has not expired, id:[{}], skip it", proposalCapsule.getID());
    }
    logger.info("Processing proposals done, oldest proposal[{}]", proposalNum);
  }

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

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L204-207)
```java
        case FORBID_TRANSFER_TO_CONTRACT: {
          manager.getDynamicPropertiesStore().saveForbidTransferToContract(entry.getValue());
          break;
        }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L359-362)
```java
        case ALLOW_OLD_REWARD_OPT: {
          manager.getDynamicPropertiesStore().saveAllowOldRewardOpt(entry.getValue());
          break;
        }
```
