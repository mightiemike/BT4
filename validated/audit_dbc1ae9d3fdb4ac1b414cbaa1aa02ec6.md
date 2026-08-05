### Title
Malicious/untrusted contract owner can front-run `TriggerSmartContract` calls by changing `consume_user_resource_percent`, shifting the energy-fee burden onto the caller - ([File: actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java])

### Summary
`UpdateSettingContractActuator` lets a deployed contract's owner (the account recorded as `origin_address` on the `ContractCapsule`) change `consume_user_resource_percent` at any time, with no delay, no cooldown, and no bound on how large a single change can be (only that it stays in `[0,100]`). This value is read live, at the moment a `TriggerSmartContract` call is executed, to decide how much of the call's energy the caller must pay for versus the contract owner. A contract owner can watch the mempool for a victim's pending call and front-run it with an `UpdateSettingContract` transaction that raises the percent to 100, shifting the entire energy cost onto the victim without the victim having any way to specify a "maximum acceptable percent," mirroring the royalty front-running bug (an untrusted/"restricted" role instantly changing a fee-like parameter that a pending, unprotected user transaction is exposed to).

### Finding Description
`UpdateSettingContractActuator.execute` writes `consume_user_resource_percent` directly into the `ContractCapsule` with no delay: [1](#0-0) 

Validation only checks that the caller is the contract's `origin_address` and that the percent is within `[0,100]`; there is no rate limiting, timelock, or restriction on how much it can move in one step: [2](#0-1) 

This value is consumed live, at the time a `TriggerSmartContract` call executes, by `VMActuator.getTotalEnergyLimitWithFixRatio` (and the float-ratio variant) to split the energy limit for the call between the caller and the contract's creator: [3](#0-2) [4](#0-3) 

When `consumeUserResourcePercent` is low (e.g. 0-10%), users calling the contract reasonably expect the creator's frozen energy to cover most of the call's cost. A malicious/compromised contract owner can observe a victim's pending `TriggerSmartContract` transaction in the mempool and front-run it with an `UpdateSettingContract` transaction raising the percent to 100 just before the victim's call is included. Because `TriggerSmartContract` contains no field constraining the acceptable `consume_user_resource_percent` (unlike, e.g., `ExchangeTransactionContract`'s `expected` slippage-protection field), the victim's transaction executes against the new, unfavorable percent with no recourse.

### Impact Explanation
The victim ends up paying for energy that was expected to be subsidized by the contract owner. Two concrete harms result:
- If the victim's `fee_limit` was sized assuming a low `consume_user_resource_percent`, the victim's transaction can run out of energy mid-execution, consuming (and paying for, in TRX) the energy used up to the point of failure, while producing no useful result — an unrecoverable loss of TRX.
- If the victim's `fee_limit` happens to be large enough, the victim now pays substantially more TRX in energy fees than intended, directly transferring value away from the caller and onto whichever side benefits from reduced creator energy usage.

This matches the pattern in the source report: a role treated as "restricted"/not-fully-trusted can, through an instantaneous parameter change with no protections on the counterparty side, cause direct loss of assets to an unprivileged user via front-running.

### Likelihood Explanation
Any account that deploys a smart contract can call `UpdateSettingContractActuator` on that contract at will, and mempool front-running of an update transaction is straightforward on java-tron given ordinary transaction propagation. No special privilege beyond being the contract's own creator is required, and there is no cooldown/timelock — the exploit is a single perfectly ordinary transaction that just needs to land before the victim's call.

### Recommendation
- Add a caller-supplied bound (similar to `expected` in `ExchangeTransactionContract`) in `TriggerSmartContract`, allowing callers to cap the `consume_user_resource_percent` they're willing to accept, and revert if the live value exceeds it at execution time.
- Alternatively/additionally, enforce a timelock or maximum per-transaction delta on `UpdateSettingContract` changes so pending calls cannot be front-run into a drastically different cost split.

### Proof of Concept
1. Contract owner deploys a contract with `consume_user_resource_percent = 0` and sufficient frozen energy, advertising cheap calls.
2. Victim broadcasts a `TriggerSmartContract` call with a `fee_limit` sized for near-zero self-funded energy.
3. Owner observes the victim's tx in the mempool and broadcasts `UpdateSettingContract` setting `consume_user_resource_percent = 100`, ensuring it is packed into an earlier block/position (front-run) — see `UpdateSettingContractActuator.execute` writing the value directly with no delay: [1](#0-0) 
4. Victim's call now executes with `consumeUserResourcePercent = 100`, so `getTotalEnergyLimitWithFixRatio`/`getTotalEnergyLimitWithFloatRatio` allocate 0 creator energy and force the victim to cover 100% of the energy: [5](#0-4) 
5. Victim's transaction either overpays in TRX energy fees or runs out of energy and fails, losing the TRX already spent on partial execution.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java (L40-49)
```java
    try {
      UpdateSettingContract usContract = any.unpack(UpdateSettingContract.class);
      long newPercent = usContract.getConsumeUserResourcePercent();
      byte[] contractAddress = usContract.getContractAddress().toByteArray();
      ContractCapsule deployedContract = contractStore.get(contractAddress);

      contractStore.put(contractAddress, new ContractCapsule(
          deployedContract.getInstance().toBuilder().setConsumeUserResourcePercent(newPercent)
              .build()));
      RepositoryImpl.removeLruCache(contractAddress);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java (L93-113)
```java
    long newPercent = contract.getConsumeUserResourcePercent();
    if (newPercent > ActuatorConstant.ONE_HUNDRED || newPercent < 0) {
      throw new ContractValidateException(
          "percent not in [0, 100]");
    }

    byte[] contractAddress = contract.getContractAddress().toByteArray();
    ContractCapsule deployedContract = contractStore.get(contractAddress);

    if (deployedContract == null) {
      throw new ContractValidateException(
          "Contract does not exist");
    }

    byte[] deployedContractOwnerAddress = deployedContract.getInstance().getOriginAddress()
        .toByteArray();

    if (!Arrays.equals(ownerAddress, deployedContractOwnerAddress)) {
      throw new ContractValidateException(
          ACCOUNT_EXCEPTION_STR + readableOwnerAddress + "] is not the owner of the contract");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L712-757)
```java
  public long getTotalEnergyLimitWithFixRatio(AccountCapsule creator, AccountCapsule caller,
      TriggerSmartContract contract, long feeLimit, long callValue)
      throws ContractValidateException {

    long callerEnergyLimit = getAccountEnergyLimitWithFixRatio(caller, feeLimit, callValue);
    if (Arrays.equals(creator.getAddress().toByteArray(), caller.getAddress().toByteArray())) {
      // when the creator calls his own contract, this logic will be used.
      // so, the creator must use a BIG feeLimit to call his own contract,
      // which will cost the feeLimit TRX when the creator's frozen energy is 0.
      return callerEnergyLimit;
    }

    long creatorEnergyLimit = 0;
    ContractCapsule contractCapsule = rootRepository
        .getContract(contract.getContractAddress().toByteArray());
    long consumeUserResourcePercent = contractCapsule.getConsumeUserResourcePercent(
        VMConfig.disableJavaLangMath());

    long originEnergyLimit = contractCapsule.getOriginEnergyLimit();
    if (originEnergyLimit < 0) {
      throw new ContractValidateException("originEnergyLimit can't be < 0");
    }

    long originEnergyLeft = 0;
    if (consumeUserResourcePercent < VMConstant.ONE_HUNDRED) {
      originEnergyLeft = rootRepository.getAccountLeftEnergyFromFreeze(creator);
      if (VMConfig.allowTvmFreeze() || VMConfig.allowTvmFreezeV2()) {
        receipt.setOriginEnergyLeft(originEnergyLeft);
      }
    }
    if (consumeUserResourcePercent <= 0) {
      creatorEnergyLimit = min(originEnergyLeft, originEnergyLimit,
          VMConfig.disableJavaLangMath());
    } else {
      if (consumeUserResourcePercent < VMConstant.ONE_HUNDRED) {
        // creatorEnergyLimit =
        // min(callerEnergyLimit * (100 - percent) / percent,
        //   creatorLeftFrozenEnergy, originEnergyLimit)

        creatorEnergyLimit = min(
            BigInteger.valueOf(callerEnergyLimit)
                .multiply(BigInteger.valueOf(VMConstant.ONE_HUNDRED - consumeUserResourcePercent))
                .divide(BigInteger.valueOf(consumeUserResourcePercent)).longValueExact(),
            min(originEnergyLeft, originEnergyLimit, VMConfig.disableJavaLangMath()),
            VMConfig.disableJavaLangMath());
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L781-805)
```java
  private long getTotalEnergyLimitWithFloatRatio(AccountCapsule creator, AccountCapsule caller,
      TriggerSmartContract contract, long feeLimit, long callValue) {

    long callerEnergyLimit = getAccountEnergyLimitWithFloatRatio(caller, feeLimit, callValue);
    if (Arrays.equals(creator.getAddress().toByteArray(), caller.getAddress().toByteArray())) {
      return callerEnergyLimit;
    }

    // creatorEnergyFromFreeze
    long creatorEnergyLimit = rootRepository.getAccountLeftEnergyFromFreeze(creator);

    ContractCapsule contractCapsule = rootRepository
        .getContract(contract.getContractAddress().toByteArray());
    long consumeUserResourcePercent = contractCapsule.getConsumeUserResourcePercent(
        VMConfig.disableJavaLangMath());

    if (creatorEnergyLimit * consumeUserResourcePercent
        > (VMConstant.ONE_HUNDRED - consumeUserResourcePercent) * callerEnergyLimit) {
      return floorDiv(
          callerEnergyLimit * VMConstant.ONE_HUNDRED, consumeUserResourcePercent,
          VMConfig.disableJavaLangMath());
    } else {
      return addExact(callerEnergyLimit, creatorEnergyLimit,
          VMConfig.disableJavaLangMath());
    }
```
