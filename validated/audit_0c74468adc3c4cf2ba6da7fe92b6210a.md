### Title
Fork-gated overflow guard for `FrozenSupply.expireTime` allows integer overflow bypass, enabling premature unfreeze of TRC10 supply - ([File: actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java])

### Finding Description
`AssetIssueActuator.execute()` computes `expireTime` with an unchecked multiplication/addition: [1](#0-0) 

The only protection against `long` overflow of `startTime + next.getFrozenDays() * FROZEN_PERIOD` is placed in `validate()`, but it is explicitly gated behind a hard-fork activation check: [2](#0-1) 

The bounds check on `frozenDays` (lines 263-268) only enforces `minFrozenSupplyTime <= frozenDays <= maxFrozenSupplyTime`, which is a dynamic, governance-configurable parameter and is not inherently tied to preventing overflow — it is independent of `FROZEN_PERIOD`'s magnitude and `startTime`'s value. The overflow-safety net (`StrictMathWrapper.addExact`) only runs `if (chainBaseManager.getForkController().pass(ForkBlockVersionEnum.VERSION_4_8_1))`. On any node/network state where this specific hard fork has not yet passed (e.g., during the rollout window, a forked/alternative chain, a private/test network that never activates `VERSION_4_8_1`, or historical replay of blocks/transactions before the fork height), the overflow-check branch is skipped entirely, and `execute()` will silently wrap the `long` addition/multiplication, producing a negative or otherwise arbitrary `expireTime` that is less than `startTime` and possibly less than the current head block time.

An attacker fully controls `startTime` (only constrained to be `> head block time`, line 206) and `frozenDays` (constrained only to be within `[minFrozenSupplyTime, maxFrozenSupplyTime]`, which are governance-settable and can be large enough to allow `frozenDays * FROZEN_PERIOD` to approach `Long.MAX_VALUE`). By choosing `frozenDays` and `startTime` such that their sum overflows on a node where the fork gate is inactive, the attacker can store a `Frozen` entry with an already-expired `expireTime`, then immediately call `UnfreezeAssetActuator` to reclaim the "frozen" TRC10 supply without ever honoring the freeze duration.

### Impact Explanation
This breaks the one-time settlement/lock-duration invariant of TRC10 frozen supply: a token issuer could unfreeze all "frozen" supply back into liquid `remainSupply`/asset balance immediately after issuance, defeating the purpose of freeze-based token distribution/vesting schemes an issuer publicly commits to. On a network topology where forked and un-forked nodes coexist during rollout, it can also produce state divergence (different nodes compute different `expireTime` for the identical transaction), a consensus-relevant invariant violation.

### Likelihood Explanation
Exploitability strictly depends on the fork-gate state: on a fully-activated mainnet where `VERSION_4_8_1` has passed for all nodes, the check is always active and the overflow path is closed. The vulnerability is real only in the exact scoped condition described in the question — pass-gate not yet active on the executing node. Given `VERSION_4_8_1` is presumably intended to be a mandatory/committee-triggered hard fork on the primary java-tron mainnet, by the time this fork passes network-wide the bug is fully mitigated for that network; however, forks that never activate this specific version (private chains, testnets frozen at an earlier version, or forked chains), or any window before mainnet activation, remain exploitable by an ordinary user submitting a normal `AssetIssueContract` transaction — no privileged access needed.

### Recommendation
Remove the fork gate and apply the `StrictMathWrapper.addExact` (and an equivalent overflow-checked multiplication for `frozenDays * FROZEN_PERIOD`) unconditionally in `validate()`, and additionally re-validate/clamp `expireTime` in `execute()` before persisting the `Frozen` capsule, rather than relying solely on a version-gated check in `validate()`. This ensures the invariant holds regardless of the fork-activation state of the executing node.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/actuator/AssetIssueActuatorOverflowTest.java
@Test
public void testFrozenExpireTimeOverflowWhenForkInactive() throws Exception {
  // Arrange: mock ForkController.pass(VERSION_4_8_1) to return false,
  // simulating a node where the fork guard is inactive.
  long headTime = dynamicStore.getLatestBlockHeaderTimestamp();
  long maxFrozenDays = dynamicStore.getMaxFrozenSupplyTime();
  // choose startTime and frozenDays near Long.MAX_VALUE / FROZEN_PERIOD boundary
  long frozenDays = maxFrozenDays; // within allowed bounds
  long startTime = Long.MAX_VALUE - frozenDays * FROZEN_PERIOD + 1000L; // forces overflow

  AssetIssueContract contract = buildContract(ownerAddress, startTime,
      startTime + 1, frozenDays, totalSupply, frozenAmount);
  AssetIssueActuator actuator = new AssetIssueActuator();
  actuator.setChainBaseManager(chainBaseManagerWithForkInactive);
  actuator.setAny(Any.pack(contract));

  actuator.validate(); // should NOT throw because fork gate is bypassed
  TransactionResultCapsule ret = new TransactionResultCapsule();
  actuator.execute(ret);

  AccountCapsule account = accountStore.get(ownerAddress);
  long expireTime = account.getInstance().getFrozenSupply(0).getExpireTime();

  // Assert the invariant is violated: expireTime wrapped to a value
  // <= startTime / <= headTime, i.e. already "expired"
  Assert.assertTrue(expireTime < startTime);
  Assert.assertTrue(expireTime <= headTime);

  // Follow-up: calling UnfreezeAssetActuator immediately succeeds
  UnfreezeAssetActuator unfreeze = new UnfreezeAssetActuator();
  unfreeze.setAny(Any.pack(UnfreezeAssetContract.newBuilder()
      .setOwnerAddress(ownerAddress).build()));
  unfreeze.validate(); // should not throw "It's not time to unfreeze"
}
```
Expected result today (with fork inactive): `validate()` passes without the overflow check, `expireTime` wraps to a value less than `startTime`/`headTime`, and `UnfreezeAssetActuator.validate()` does not reject the premature unfreeze — confirming the exploit path.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L100-108)
```java
      long startTime = assetIssueContract.getStartTime();

      while (iterator.hasNext()) {
        FrozenSupply next = iterator.next();
        long expireTime = startTime + next.getFrozenDays() * FROZEN_PERIOD;
        Frozen newFrozen = Frozen.newBuilder()
            .setFrozenBalance(next.getFrozenAmount())
            .setExpireTime(expireTime)
            .build();
```

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L269-280)
```java
      // make sure FrozenSupply.expireTime not overflow
      if (chainBaseManager.getForkController().pass(ForkBlockVersionEnum.VERSION_4_8_1)) {
        long frozenPeriod = next.getFrozenDays() * FROZEN_PERIOD;
        try {
          StrictMathWrapper.addExact(assetIssueContract.getStartTime(), frozenPeriod);
        } catch (ArithmeticException e) {
          throw new ContractValidateException(
              "Start time and frozen days would cause expire time overflow");
        }
      }
      remainSupply -= next.getFrozenAmount();
    }
```
