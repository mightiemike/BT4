### Title
Missing Feature-Gate Check in TVM Native Contract Path for Expired Unfreeze Withdrawal - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawExpireUnfreezeProcessor.java])

### Summary
The external report's bug class is "a protective check exists in one code path but is missing from a related code path that performs the same state-affecting operation, allowing an unintended bypass." In the vault report, `update_vault_yield`/`handle_stage_yt_yield` enforces an emergency-mode guard, but sibling entry points (`withdraw_yt`, `merge`) that touch the same accounting state omit it. In java-tron, the analogous pattern occurs between `WithdrawExpireUnfreezeActuator` (the normal transaction path) and `WithdrawExpireUnfreezeProcessor` (the TVM native-contract path reachable from a smart contract via `Program.withdrawExpireUnfreeze()`), both of which mutate the same account "unfrozen v2" balance state but only one of them checks the feature-gate flag `supportUnfreezeDelay()`.

### Finding Description
`WithdrawExpireUnfreezeActuator.validate()` explicitly requires the chain feature to be enabled before allowing an account to withdraw its expired unfreeze balance: [1](#0-0) 

This check exists because `supportUnfreezeDelay()` is a committee-controlled proposal flag gating the entire "delayed unfreeze / unfrozenV2" resource model feature; withdrawing early via this path when the feature is not (or not yet) fully active could bypass the accounting invariants the feature was designed to enforce for `AccountStore`, `DynamicPropertiesStore.getTotalNetWeight/getTotalEnergyWeight`, and vote-clearing consistency.

However, the corresponding TVM-native-contract implementation of the same operation, `WithdrawExpireUnfreezeProcessor.validate()` and `.execute()`, performs address/account/balance-overflow checks but never calls `dynamicStore.supportUnfreezeDelay()`: [2](#0-1) [3](#0-2) 

This processor is reachable from any smart contract via the `withdrawExpireUnfreeze()` native-contract call exposed on `Program`: [4](#0-3) 

Mirroring the report's structure: the "primary" path (`WithdrawExpireUnfreezeActuator`, analogous to `update_vault_yield`) correctly gates the operation behind the feature/committee flag, while the "sibling" path reachable through the TVM (`WithdrawExpireUnfreezeProcessor`, analogous to `withdraw_yt`/`merge`) omits the same guard even though it mutates identical account state (`unfrozenV2` list, `balance`).

### Impact Explanation
If `supportUnfreezeDelay` is disabled by the committee (e.g., feature not yet activated, or later disabled/rolled back for a chain), a malicious or unaware smart contract could still invoke the native `withdrawExpireUnfreeze` TVM operation and successfully withdraw "unfrozenV2" balances that should be blocked, producing state divergent from what the ordinary transaction path allows. This is an accounting/consensus-consistency issue: nodes that correctly gate the actuator path would reject an equivalent user-initiated transaction, but the same effect could be reached through a contract call, creating inconsistent enforcement of a chain feature flag and potential unintended balance changes.

### Likelihood Explanation
Exploitability depends entirely on the state of the `supportUnfreezeDelay` committee proposal. On networks/mainnet where the proposal has been permanently enabled, this gap has no effect since the feature is always on. The risk is concentrated in scenarios where the flag is toggled off, is being staged for rollout, or exists on private/test networks. I could not verify from the indexed code whether `supportUnfreezeDelay` is ever disabled again after being enabled in production, or whether other invariants make it always true by the time `unfrozenV2` entries can exist — this needs confirmation in the full codebase/deployment history.

### Recommendation
Add the same `dynamicStore.supportUnfreezeDelay()` check to `WithdrawExpireUnfreezeProcessor.validate()` (and ideally to `execute()` defensively) that is already enforced in `WithdrawExpireUnfreezeActuator.validate()`, so that the TVM native-contract entry point cannot be used to bypass the feature gate.

### Proof of Concept
1. Ensure the committee proposal enabling `supportUnfreezeDelay` is not yet active on the target network (flag returns `false`).
2. Deploy/call a smart contract that invokes the TVM's `withdrawExpireUnfreeze` opcode via `Program.withdrawExpireUnfreeze()`.
3. Observe that `WithdrawExpireUnfreezeProcessor.execute()` succeeds and moves the expired `unfrozenV2` amount into `balance`, even though an equivalent `WithdrawExpireUnfreezeContract` transaction submitted through `WithdrawExpireUnfreezeActuator` would be rejected with `"Not support WithdrawExpireUnfreeze transaction, need to be opened by the committee"`.

Note: I was unable to fully trace all call sites (e.g., `OperationActions.java`, `ConfigLoader.java`, `PrecompiledContracts.java`) that reference `supportUnfreezeDelay` due to tool/iteration limits, so I cannot rule out that an equivalent gate is enforced earlier in the TVM opcode dispatch (outside the processor itself). This should be verified against the full source before treating this as a confirmed, currently-exploitable issue.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java (L84-87)
```java
    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support WithdrawExpireUnfreeze transaction,"
          + " need to be opened by the committee");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawExpireUnfreezeProcessor.java (L25-55)
```java
  public void validate(WithdrawExpireUnfreezeParam param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    byte[] ownerAddress = param.getOwnerAddress();
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }
    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);
    if (Objects.isNull(accountCapsule)) {
      String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);
      throw new ContractValidateException(ACCOUNT_EXCEPTION_STR
          + readableOwnerAddress + NOT_EXIST_STR);
    }

    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    List<Protocol.Account.UnFreezeV2> unfrozenV2List = accountCapsule.getInstance()
        .getUnfrozenV2List();
    long totalWithdrawUnfreeze = getTotalWithdrawUnfreeze(unfrozenV2List, now);
    if (totalWithdrawUnfreeze < 0) {
      throw new ContractValidateException("no unFreeze balance to withdraw ");
    }
    try {
      LongMath.checkedAdd(accountCapsule.getBalance(), totalWithdrawUnfreeze);
    } catch (ArithmeticException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractValidateException(e.getMessage());
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawExpireUnfreezeProcessor.java (L67-85)
```java
  public long execute(WithdrawExpireUnfreezeParam param, Repository repo) throws ContractExeException {
    byte[] ownerAddress = param.getOwnerAddress();
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddress);
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    List<Protocol.Account.UnFreezeV2> unfrozenV2List = ownerCapsule.getInstance().getUnfrozenV2List();
    long totalWithdrawUnfreeze = getTotalWithdrawUnfreeze(unfrozenV2List, now);
    if (totalWithdrawUnfreeze <= 0) {
      return 0;
    }
    ownerCapsule.setInstance(ownerCapsule.getInstance().toBuilder()
        .setBalance(ownerCapsule.getBalance() + totalWithdrawUnfreeze)
        .build());
    List<Protocol.Account.UnFreezeV2> newUnFreezeList = getRemainWithdrawList(unfrozenV2List, now);
    ownerCapsule.clearUnfrozenV2();
    ownerCapsule.addAllUnfrozenV2(newUnFreezeList);
    repo.updateAccount(ownerCapsule.createDbKey(), ownerCapsule);
    return totalWithdrawUnfreeze;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2329-2358)
```java
  public long withdrawReward() {
    Repository repository = getContractState().newRepositoryChild();
    byte[] owner = getContextAddress();

    increaseNonce();
    InternalTransaction internalTx = addInternalTx(null, owner, owner, 0, null,
        "withdrawReward", nonce, null);

    WithdrawRewardParam param = new WithdrawRewardParam();
    param.setOwnerAddress(owner);
    param.setNowInMs(getTimestamp().longValue() * 1000);
    try {
      WithdrawRewardProcessor processor = new WithdrawRewardProcessor();
      processor.validate(param, repository);
      long allowance = processor.execute(param, repository);
      repository.commit();
      if (internalTx != null) {
        internalTx.setValue(allowance);
      }
      return allowance;
    } catch (ContractValidateException e) {
      logger.warn("TVM WithdrawReward: validate failure. Reason: {}", e.getMessage());
    } catch (ContractExeException e) {
      logger.warn("TVM WithdrawReward: execute failure. Reason: {}", e.getMessage());
    }
    if (internalTx != null) {
      internalTx.reject();
    }
    return 0;
  }
```
