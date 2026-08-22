### Title
Missing bound check on `frozenDuration` in `FreezeBalanceProcessor` allows expire-time overflow, bypassing the freeze/lock waiting period - (File: actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java)

### Summary
The TVM native-contract freeze-balance path (`FreezeBalanceProcessor`, reachable from a smart contract's freeze-balance opcode/precompiled call, i.e. from any unprivileged transaction that triggers contract execution) validates `frozenBalance`, `frozenCount`, `resourceType` and the receiver account, but never validates `param.getFrozenDuration()`. This mirrors the Juicebox `mustStartAtOrAfter` bug class: an attacker-controlled duration value is added, unchecked, into a time computation (`now + duration * period`) that is later relied upon as a waiting-period/lock gate.

### Finding Description
`FreezeBalanceProcessor.validate()` checks several parameters but has no bound (min/max, non-negative) check on `frozenDuration`: [1](#0-0) 

`execute()` then computes the expire time directly from the unchecked `frozenDuration`: [2](#0-1) 

`frozenDuration` is a plain `long` field on `FreezeBalanceParam` with no built-in constraint: [3](#0-2) 

If a caller (via the TVM opcode/native contract that populates this param from smart-contract-supplied data) can set an arbitrarily large or negative `frozenDuration`, the multiplication `param.getFrozenDuration() * FROZEN_PERIOD` combined with the addition to `nowInMs` can overflow a Java `long`, wrapping around to a small or even negative `expireTime`. This is exactly the root cause pattern in the referenced Juicebox finding: an unchecked numeric parameter is folded into a start/expire timestamp computation, and overflow lets the attacker land on an already-past timestamp, defeating the intended waiting period.

I was not able to fully trace, within the available indexed context, the exact TVM opcode/caller in `Program.java` that populates `FreezeBalanceParam.frozenDuration` from contract-supplied bytes, nor confirm whether any upstream caller performs its own bound check before invoking `FreezeBalanceProcessor.validate()`. This should be verified directly in the full source (the index only surfaced match counts for `Program.java`, not the relevant code region) before treating this as conclusively exploitable.

### Impact Explanation
If `frozenDuration` reaches `FreezeBalanceProcessor.execute()` unchecked and can be influenced to a value that overflows the `expireTime` computation, an attacker could:
- Set `expireTime` to a value at or before "now", immediately satisfying the "unfreeze allowed" / "lock expired" condition instead of waiting out `FROZEN_PERIOD * frozenDuration`.
- Corrupt bandwidth/energy accounting (`setFrozenForBandwidth`/`setFrozenForEnergy`, `addTotalNetWeight`/`addTotalEnergyWeight`) with an incoherent expire time, potentially affecting resource/vote weight bookkeeping network-wide.

This falls under "asset or accounting corruption" via a native contract state transition reachable from an ordinary transaction.

### Likelihood Explanation
Likelihood is moderate but unconfirmed: it depends on whether the TVM-level caller that constructs `FreezeBalanceParam` from contract bytecode/inputs already clamps `frozenDuration` before calling `validate()`/`execute()`. Without seeing that caller code, I cannot confirm this is reachable end-to-end with a fully attacker-controlled value; this is the main open question for further verification via a full source checkout.

### Recommendation
Add explicit bound checks on `frozenDuration` in `FreezeBalanceProcessor.validate()`, mirroring the legacy `FreezeBalanceActuator`'s min/max duration checks (e.g., reject `frozenDuration <= 0` and `frozenDuration` beyond the maximum allowed freeze days), and use `Math.addExact`/`Math.multiplyExact` (or `LongMath.checkedAdd`/`checkedMultiply`, already used elsewhere in the codebase, e.g. `WithdrawExpireUnfreezeActuator`) when computing `expireTime` so overflow throws instead of silently wrapping.

### Proof of Concept
Conceptual PoC (pending confirmation of the exact TVM entry point that sets `frozenDuration`):
1. Deploy/call a contract path that invokes the native freeze-balance function with `frozenDuration` set to a value such that `frozenDuration * FROZEN_PERIOD + now` overflows `Long.MAX_VALUE` and wraps to a timestamp ≤ current block time.
2. Observe that the resulting `FreezeV2`/`Frozen` record's `expireTime` is already "expired", allowing immediate unfreeze/withdrawal or unexpected resource weight changes, bypassing the intended lock duration — analogous to the Juicebox `mustStartAtOrAfter` overflow bypassing the ballot waiting period.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L21-52)
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
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L73-77)
```java
  public void execute(FreezeBalanceParam param,  Repository repo) {
    // calculate expire time
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();
    long nowInMs = dynamicStore.getLatestBlockHeaderTimestamp();
    long expireTime = nowInMs + param.getFrozenDuration() * FROZEN_PERIOD;
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/param/FreezeBalanceParam.java (L43-49)
```java
  public long getFrozenDuration() {
    return frozenDuration;
  }

  public void setFrozenDuration(long frozenDuration) {
    this.frozenDuration = frozenDuration;
  }
```
