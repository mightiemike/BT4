## Analog Found

### Title
SELFDESTRUCT (`SUICIDE`) instantly reassigns frozen v2 stake to an attacker-chosen address, bypassing all normal unfreeze-delay, lock-period, and delegate-eligibility checks - (File: `actuator/src/main/java/org/tron/core/vm/program/Program.java`)

### Summary
The ENS bug pattern is: destroy/burn the current ownership record, then recreate ownership state using a caller-supplied target address, without validating that target against the previous owner or against any active "cannot transfer" restriction. The equivalent pattern in java-tron is the `SUICIDE`/`SELFDESTRUCT` opcode handling in the TVM: it destroys the contract's frozen v2 stake bookkeeping and re-creates it directly under an arbitrary, caller-supplied "inheritor" (obtainer) address, without going through any of the checks that the dedicated resource-transfer actuators (`DelegateResourceActuator`/`DelegateResourceProcessor`, `UnfreezeBalanceV2Actuator`) enforce.

### Finding Description
When a contract executes `SELFDESTRUCT`, `Program.suicide` (which calls `transferFrozenV2BalanceToInheritor`) moves the contract's still-frozen (staked) v2 balance for BANDWIDTH/ENERGY/TRON_POWER directly into the caller-chosen beneficiary's `FrozenV2` list: [1](#0-0) 

This is functionally a full transfer of "locked" stake ownership from `ownerAddr` to `inheritorAddr` (an address entirely controlled by the calling contract's bytecode/caller), executed atomically inside `clearOwnerFreezeV2` + `repo.updateAccount` calls: [2](#0-1) 

Compare this to the two legitimate, purpose-built pathways for moving/realizing frozen v2 stake, both of which impose restrictions this SUICIDE path skips entirely:

1. `DelegateResourceProcessor.validate` explicitly forbids delegating resources to a contract address and enforces lock-period bounds: [3](#0-2) 

2. `DelegateResourceActuator`/`UnDelegateResourceActuator` enforce a `lock`/`lockPeriod` mechanism that prevents early un-delegation, analogous to the ENS `CANNOT_TRANSFER` fuse: [4](#0-3) 

3. `UnfreezeBalanceV2Actuator`/`WithdrawExpireUnfreezeActuator` require frozen stake to go through an `unfreezeDelayDays`-long unfreeze period before it becomes ordinary, transferable balance: [5](#0-4) 

None of these checks (no-contract-receiver restriction, lock-period enforcement, unfreeze delay) are consulted by `transferFrozenV2BalanceToInheritor`. Just like the ENS `_prepareUpgrade`/`upgradeETH2LD` flow burns the node and hands ownership to an unvalidated, caller-supplied `wrappedOwner`, `Program.suicide` destroys the contract's frozen-stake bookkeeping and hands it to an unvalidated, caller-supplied `obtainer` address — the "upgraded" (destination) state is set purely from an attacker-controlled parameter with no cross-check against the resource-transfer invariants that would otherwise apply.

### Impact Explanation
This allows a contract deployer to instantly and directly transfer staked/frozen TRX resource weight (bandwidth/energy/TRON Power) to any address of their choosing — including to another contract address (explicitly disallowed for normal delegation) — without waiting the mandated unfreeze delay and without any lock-period restriction. Because `FrozenV2` balance backs `TotalNetWeight`/`TotalEnergyWeight`/voting power (TRON Power) accounting, an attacker can use this to instantly re-home stake/voting weight between addresses under their control in ways the normal actuators are specifically designed to prevent (e.g., preventing resource concentration in contract addresses, or enforcing lock commitments), corrupting the intended resource/vote-weight accounting invariants.

### Likelihood Explanation
`SELFDESTRUCT` is a standard, unprivileged TVM opcode reachable by any account that deploys and triggers a smart contract via a normal broadcast transaction; no special permission or witness/validator role is required, and no feature flag other than the already-enabled `allowTvmFreezeV2` gate is needed.

### Recommendation
Route the frozen v2 stake transfer performed during `SELFDESTRUCT` through the same validation used by `DelegateResourceProcessor`/`UnfreezeBalanceV2Actuator` (or explicitly forbid transferring still-locked/frozen v2 stake to arbitrary "inheritor" addresses on suicide, only allowing it after the normal unfreeze delay, and disallowing contract-address beneficiaries), so the destination address is validated against the same restrictions that apply to normal resource-transfer paths.

### Proof of Concept
1. Deploy a contract, freeze TRX for bandwidth/energy/TRON Power via `freezeBalanceV2` under the contract's own address (`allowTvmFreezeV2` enabled).
2. From the contract, call `SELFDESTRUCT(beneficiary)` where `beneficiary` is an address chosen by the attacker (can be a second contract, which would be disallowed if attempted via `delegateResource`).
3. Observe that `Program.suicide` → `transferFrozenV2BalanceToInheritor` moves the entire `FrozenV2` stake into `beneficiary`'s `FrozenV2` list immediately, with no unfreeze delay, no lock check, and no "receiver must not be a contract" check — reproducing the ENS pattern of an unauthorized/unchecked ownership handoff during a destructive state transition.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L620-641)
```java
  private long transferFrozenV2BalanceToInheritor(byte[] ownerAddr, byte[] inheritorAddr, Repository repo) {
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddr);
    AccountCapsule inheritorCapsule = repo.getAccount(inheritorAddr);
    long now = repo.getHeadSlot();

    // transfer frozen resource
    ownerCapsule.getFrozenV2List().stream()
        .filter(freezeV2 -> freezeV2.getAmount() > 0)
        .forEach(
            freezeV2 -> {
              switch (freezeV2.getType()) {
                case BANDWIDTH:
                  inheritorCapsule.addFrozenBalanceForBandwidthV2(freezeV2.getAmount());
                  break;
                case ENERGY:
                  inheritorCapsule.addFrozenBalanceForEnergyV2(freezeV2.getAmount());
                  break;
                case TRON_POWER:
                  inheritorCapsule.addFrozenForTronPowerV2(freezeV2.getAmount());
                  break;
              }
            });
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L677-681)
```java
    clearOwnerFreezeV2(ownerCapsule);
    repo.updateAccount(ownerCapsule.createDbKey(), ownerCapsule);
    repo.updateAccount(inheritorCapsule.createDbKey(), inheritorCapsule);
    return expireUnfrozenBalance;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L95-114)
```java
    byte[] receiverAddress = param.getReceiverAddress();

    if (!DecodeUtil.addressValid(receiverAddress)) {
      throw new ContractValidateException("Invalid receiverAddress");
    }
    if (Arrays.equals(receiverAddress, ownerAddress)) {
      throw new ContractValidateException(
          "receiverAddress must not be the same as ownerAddress");
    }
    AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
    if (receiverCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(receiverAddress);
      throw new ContractValidateException(
          ActuatorConstant.ACCOUNT_EXCEPTION_STR
              + readableOwnerAddress + NOT_EXIST_STR);
    }
    if (receiverCapsule.getType() == Protocol.AccountType.Contract) {
      throw new ContractValidateException(
          "Do not allow delegate resources to contract addresses");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L211-219)
```java
    boolean lock = delegateResourceContract.getLock();
    if (lock && dynamicStore.supportMaxDelegateLockPeriod()) {
      long lockPeriod = getLockPeriod(true, delegateResourceContract);
      long maxDelegateLockPeriod = dynamicStore.getMaxDelegateLockPeriod();
      if (lockPeriod < 0 || lockPeriod > maxDelegateLockPeriod) {
        throw new ContractValidateException(
            "The lock period of delegate resource cannot be less than 0 and cannot exceed "
                + maxDelegateLockPeriod + "!");
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java (L229-234)
```java

```
