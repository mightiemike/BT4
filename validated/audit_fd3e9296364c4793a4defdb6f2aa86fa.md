## Analysis

The Li.Fi bug class — functions intended to be *value-free* (token/data-only) that fail to check `msg.value == 0`, letting native currency get trapped in a contract that has no way to move it out — has a direct structural analog in java-tron's TVM precompiled-contract call path.

### Finding

**TRX sent via low-level `CALL` with value to TVM read-only precompiled system contracts (RewardBalance, IsSrCandidate, VoteCount, UsedVoteCount, ReceivedVoteCount, TotalVoteCount, GetChainParameter, AvailableUnfreezeV2Size, UnfreezableBalanceV2, ExpireUnfreezeBalanceV2, DelegatableResource, ResourceV2, CheckUnDelegateResource, ResourceUsage, TotalResource, TotalDelegatedResource, TotalAcquiredResource) is permanently locked** — ([File: actuator/src/main/java/org/tron/core/vm/program/Program.java])

### Finding Description
`Program.callToPrecompiledAddress` handles any `CALL`/`CALLCODE`/`DELEGATECALL` targeted at a fixed precompiled address. Before invoking the precompiled contract logic, it unconditionally transfers the message's `endowment` (i.e. `msg.value`) from the caller to the fixed `contextAddress` if `endowment > 0`, with no check that the target precompile is value-accepting: [1](#0-0) 

The precompiled contracts resolved through `PrecompiledContracts.getContractForAddress` for addresses such as `rewardBalanceAddr`, `isSrCandidateAddr`, `voteCountAddr`, `usedVoteCountAddr`, `receivedVoteCountAddr`, `totalVoteCountAddr`, and the various FreezeV2 query addresses are all pure query/view functions: [2](#0-1) 

None of these implementations consume, refund, or reject a non-zero `callValue`/endowment — unlike the bridging facets in the Li.Fi report, none of these functions check that the attached value is zero before proceeding. Once `MUtil.transfer` credits the balance to the fixed precompile address (e.g. `0x1000005`–`0x1000015`), that balance is unrecoverable: these addresses have no private key and no protocol-level withdrawal path exists for a fixed precompile address's balance: [3](#0-2) 

### Impact Explanation
Any user (or, more likely, a smart contract developer who mistakenly calls `voteContractAddress.call.value(x)(...)` or similar low-level call syntax while interacting with TVM's native vote/resource precompiles) can cause TRX to be irreversibly locked at a system address that has no owner and no spend path. This is a fund-loss bug reachable purely via a normal `TriggerSmartContract` transaction — no privileged actor is required.

### Likelihood Explanation
Likelihood is moderate: developers writing contracts that interface with TVM's vote/reward/freeze precompiles (as shown in the codebase's own `Vote` test contract, which calls these addresses via low-level `.call`/`.delegatecall`) could easily attach value by mistake, especially if wrapping these low-level calls in a `payable` function that forwards `msg.value`. The result is silent, non-reverting fund loss rather than an explicit failure, mirroring the "medium severity, mistakenly attached value" pattern in the original report.

### Recommendation
In `Program.callToPrecompiledAddress`, reject (revert/return failure) calls to fixed precompiled addresses that carry non-zero `endowment` unless the specific precompile is designed to accept and use TRX. At minimum, add a check equivalent to `if (endowment > 0) { stackPushZero(); return; }` for all read-only precompiled contracts before performing the balance transfer.

### Proof of Concept
1. Deploy a contract with a `payable` function that does `address(0x1000005).call{value: 1000}(dataForRewardBalanceQuery)` (RewardBalance precompile).
2. Trigger the function with `callValue = 1000` sun.
3. Observe: the query executes successfully (returns the reward balance), and the account balance of address `0x0000...1000005` increases by 1000 sun.
4. No mechanism in the codebase (actuators, precompiles, or withdrawal contracts) allows retrieving TRX from address `0x1000005`; the funds are permanently frozen.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1701-1721)
```java
    // Charge for endowment - is not reversible by rollback
    if (!ArrayUtils.isEmpty(senderAddress) && !ArrayUtils.isEmpty(contextAddress)
        && senderAddress != contextAddress && msg.getEndowment().value().longValueExact() > 0) {
      if (!isTokenTransfer) {
        try {
          MUtil.transfer(deposit, senderAddress, contextAddress,
              msg.getEndowment().value().longValueExact());
        } catch (ContractValidateException e) {
          throw new BytecodeExecutionException("transfer failure");
        }
      } else {
        try {
          VMUtils
              .validateForSmartContract(deposit, senderAddress, contextAddress, tokenId, endowment);
        } catch (ContractValidateException e) {
          throw new BytecodeExecutionException(VALIDATE_FOR_SMART_CONTRACT_FAILURE, e.getMessage());
        }
        deposit.addTokenBalance(senderAddress, tokenId, -endowment);
        deposit.addTokenBalance(contextAddress, tokenId, endowment);
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L272-334)
```java
    if (VMConfig.allowTvmVote() && address.equals(rewardBalanceAddr)) {
      return rewardBalance;
    }
    if (VMConfig.allowTvmVote() && address.equals(isSrCandidateAddr)) {
      return isSrCandidate;
    }
    if (VMConfig.allowTvmVote() && address.equals(voteCountAddr)) {
      return voteCount;
    }
    if (VMConfig.allowTvmVote() && address.equals(usedVoteCountAddr)) {
      return usedVoteCount;
    }
    if (VMConfig.allowTvmVote() && address.equals(receivedVoteCountAddr)) {
      return receivedVoteCount;
    }
    if (VMConfig.allowTvmVote() && address.equals(totalVoteCountAddr)) {
      return totalVoteCount;
    }
    if (VMConfig.allowTvmCompatibleEvm() && address.equals(ethRipemd160Addr)) {
      return ethRipemd160;
    }
    if (VMConfig.allowTvmCompatibleEvm() && address.equals(blake2FAddr)) {
      return blake2F;
    }
    if (VMConfig.allowTvmOsaka() && address.equals(p256VerifyAddr)) {
      return p256Verify;
    }

    if (VMConfig.allowTvmFreezeV2()) {
      if (address.equals(getChainParameterAddr)) {
        return getChainParameter;
      }
      if (address.equals(availableUnfreezeV2SizeAddr)) {
        return availableUnfreezeV2Size;
      }
      if (address.equals(unfreezableBalanceV2Addr)) {
        return unfreezableBalanceV2;
      }
      if (address.equals(expireUnfreezeBalanceV2Addr)) {
        return expireUnfreezeBalanceV2;
      }
      if (address.equals(delegatableResourceAddr)) {
        return delegatableResource;
      }
      if (address.equals(resourceV2Addr)) {
        return resourceV2;
      }
      if (address.equals(checkUnDelegateResourceAddr)) {
        return checkUnDelegateResource;
      }
      if (address.equals(resourceUsageAddr)) {
        return resourceUsage;
      }
      if (address.equals(totalResourceAddr)) {
        return totalResource;
      }
      if (address.equals(totalDelegatedResourceAddr)) {
        return totalDelegatedResource;
      }
      if (address.equals(totalAcquiredResourceAddr)) {
        return totalAcquiredResource;
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/MUtil.java (L18-26)
```java
  public static void transfer(Repository deposit, byte[] fromAddress, byte[] toAddress, long amount)
      throws ContractValidateException {
    if (0 == amount) {
      return;
    }
    VMUtils.validateForSmartContract(deposit, fromAddress, toAddress, amount);
    deposit.addBalance(toAddress, amount);
    deposit.addBalance(fromAddress, -amount);
  }
```
