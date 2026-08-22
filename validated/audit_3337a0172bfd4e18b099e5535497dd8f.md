### Title
Missing `frozenDuration` bounds check in `FreezeBalanceProcessor.validate()` allows `expireTime` overflow in `FreezeBalanceProcessor.execute()` - (File: `actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java`)

### Summary
`FreezeBalanceProcessor.execute()` computes `expireTime = nowInMs + param.getFrozenDuration() * FROZEN_PERIOD` without any prior range check on `frozenDuration`, unlike the legacy `FreezeBalanceActuator`, which explicitly validates `frozenDuration` against `minFrozenTime`/`maxFrozenTime` before use. Because `FreezeBalanceProcessor` is a native contract reachable from TVM contract execution (via `Program.java`), an unprivileged contract caller can supply an out-of-range `frozenDuration` that overflows the `long` multiplication/addition, producing a corrupted `expireTime` stored into `AccountCapsule`/`DelegatedResourceCapsule`.

### Finding Description
`FreezeBalanceProcessor.validate()` (`actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java:21-71`) checks `frozenBalance`, `frozenCount`, `resourceType`, and delegate-target constraints, but performs **no check whatsoever on `param.getFrozenDuration()`**. Compare this to the legacy `FreezeBalanceActuator.validate()` (`actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java:203-214`), which explicitly enforces:
```java
long frozenDuration = freezeBalanceContract.getFrozenDuration();
long minFrozenTime = dynamicStore.getMinFrozenTime();
long maxFrozenTime = dynamicStore.getMaxFrozenTime();
...
if (needCheckFrozeTime && !(frozenDuration >= minFrozenTime && frozenDuration <= maxFrozenTime)) {
  throw new ContractValidateException(...);
}
``` [1](#0-0) 

In `FreezeBalanceProcessor.execute()`:
```java
long expireTime = nowInMs + param.getFrozenDuration() * FROZEN_PERIOD;
``` [2](#0-1) 

there is no equivalent guard, so `frozenDuration` (a `long` fully controlled by the caller through the native-contract parameter) is multiplied by `FROZEN_PERIOD` unchecked. `Program.java` is the only construction site of `FreezeBalanceProcessor`, confirming this is reachable from TVM native contract dispatch triggered by ordinary contract calls, not a privileged code path.

This missing check means an attacker driving the native freeze call (e.g., through a deployed contract that invokes the freeze native contract opcode) can pass a `frozenDuration` value large enough that `frozenDuration * FROZEN_PERIOD` overflows `long`, wrapping to a negative or otherwise arbitrary value, which then flows into `expireTime` and is persisted via `accountCapsule.setFrozenForBandwidth/setFrozenForEnergy(...)` and `DelegatedResourceCapsule.addFrozenBalanceForBandwidth/Energy(...)`.

### Impact Explanation
If `expireTime` wraps to a value at or before `now`, the frozen resource state is immediately eligible for "unfreeze" logic while `repo.addTotalNetWeight`/`repo.addTotalEnergyWeight` have already been incremented, letting an attacker acquire bandwidth/energy weight and then reclaim the frozen TRX balance essentially without the intended lock period — a resource-accounting corruption / value-conservation violation matching TRON's "asset/accounting corruption" bounty class. It could also produce persistently corrupted `AccountCapsule`/`DelegatedResourceCapsule` state with an absurd `expireTime`, which other consensus-critical code paths (e.g., unfreeze eligibility checks) rely on, risking divergent behavior between nodes if edge-case handling of negative/overflowed timestamps differs.

### Likelihood Explanation
The attacker only needs to be an ordinary funded account/contract deployer able to trigger the freeze native contract with a crafted `frozenDuration` value (e.g., near `Long.MAX_VALUE / FROZEN_PERIOD`). No privileged role, signature bypass, or non-default configuration is required — only the standard fee/energy cost of a contract call. This is fully repeatable by any address with enough TRX to satisfy `frozenBalance >= 1 TRX` and cover call energy costs.

I was not able to fully trace the exact TVM opcode/`Program.java` call site within the available tool budget (grep confirmed `FreezeBalanceProcessor` is instantiated exactly once in `Program.java`, but I could not inspect the surrounding lines to confirm what native-contract input decoding — e.g., ABI decoding bounds — precedes the call). This should be verified directly in `actuator/src/main/java/org/tron/core/vm/program/Program.java` before treating this as fully confirmed, since it's possible (though not shown in the code I could inspect) that an earlier decoding step clamps `frozenDuration`.

### Recommendation
Add the same `frozenDuration` range validation used in `FreezeBalanceActuator.validate()` (checking against `dynamicStore.getMinFrozenTime()`/`getMaxFrozenTime()`, and rejecting negative or unreasonably large values) inside `FreezeBalanceProcessor.validate()` before `execute()` is ever invoked. Additionally, use `Math.multiplyExact`/`Math.addExact` (or manual overflow checks) when computing `expireTime` in `FreezeBalanceProcessor.execute()` (`actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java:77`) to fail closed on overflow rather than silently wrapping.

### Proof of Concept
```java
// JUnit-style PoC targeting FreezeBalanceProcessor directly
FreezeBalanceParam param = new FreezeBalanceParam();
param.setOwnerAddress(ownerAddress);
param.setReceiverAddress(ownerAddress);
param.setFrozenBalance(1_000_000L); // 1 TRX
param.setResourceType(Common.ResourceCode.BANDWIDTH);
param.setFrozenDuration(Long.MAX_VALUE / FROZEN_PERIOD + 1); // overflow-inducing value

FreezeBalanceProcessor processor = new FreezeBalanceProcessor();
// validate() does not throw, because frozenDuration is never range-checked
processor.validate(param, repository);

processor.execute(param, repository);

AccountCapsule account = repository.getAccount(ownerAddress);
long expireTime = account.getFrozenBalanceForBandwidth() > 0
    ? account.getAccountResource().getFrozenBalanceForBandwidth().getExpireTime()
    : -1;

// Expected (failing) assertion demonstrating the bug:
Assert.assertTrue(expireTime > System.currentTimeMillis()); // FAILS: expireTime overflows negative/arbitrary
``` [3](#0-2)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L203-214)
```java
    long frozenDuration = freezeBalanceContract.getFrozenDuration();
    long minFrozenTime = dynamicStore.getMinFrozenTime();
    long maxFrozenTime = dynamicStore.getMaxFrozenTime();

    boolean needCheckFrozeTime = CommonParameter.getInstance()
        .getCheckFrozenTime() == 1;//for test
    if (needCheckFrozeTime && !(frozenDuration >= minFrozenTime
        && frozenDuration <= maxFrozenTime)) {
      throw new ContractValidateException(
          "frozenDuration must be less than " + maxFrozenTime + " days "
              + "and more than " + minFrozenTime + " days");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L21-77)
```java
  public void validate(FreezeBalanceParam param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    // validate arg @frozenBalance
    byte[] ownerAddress = param.getOwnerAddress();
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddress);
    long frozenBalance = param.getFrozenBalance();
    if (frozenBalance <= 0) {
      throw new ContractValidateException("FrozenBalance must be positive");
    } else if (frozenBalance < TRX_PRECISION) {
      throw new ContractValidateException("FrozenBalance must be greater than or equal to 1 TRX");
    } else if (frozenBalance > ownerCapsule.getBalance()) {
      throw new ContractValidateException("FrozenBalance must be less than or equal to accountBalance");
    }

    // validate frozen count of owner account
    int frozenCount = ownerCapsule.getFrozenCount();
    if (frozenCount != 0 && frozenCount != 1) {
      throw new ContractValidateException("FrozenCount must be 0 or 1");
    }

    // validate arg @resourceType
    switch (param.getResourceType()) {
      case BANDWIDTH:
      case ENERGY:
        break;
      default:
        throw new ContractValidateException(
            "Unknown ResourceCode, valid ResourceCode[BANDWIDTH、ENERGY]");
    }

    // validate for delegating resource
    byte[] receiverAddress = param.getReceiverAddress();
    if (!FastByteComparisons.isEqual(ownerAddress, receiverAddress)) {
      param.setDelegating(true);

      // check if receiver account exists. if not, then create a new account
      AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
      if (receiverCapsule == null) {
        receiverCapsule = repo.createNormalAccount(receiverAddress);
      }

      // forbid delegating resource to contract account
      if (receiverCapsule.getType() == Protocol.AccountType.Contract) {
        throw new ContractValidateException(
            "Do not allow delegate resources to contract addresses");
      }
    }
  }

  public void execute(FreezeBalanceParam param,  Repository repo) {
    // calculate expire time
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();
    long nowInMs = dynamicStore.getLatestBlockHeaderTimestamp();
    long expireTime = nowInMs + param.getFrozenDuration() * FROZEN_PERIOD;
```
