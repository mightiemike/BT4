## Title
FreezeBalance Actuator Resets the Unlock Time of Already-Frozen TRX on Repeated Freezing - (File: `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java`)

## Summary
The legacy `FreezeBalanceContract` flow (`FreezeBalanceActuator`, mirrored in the TVM native `FreezeBalanceProcessor`) merges any newly frozen TRX with the account's existing frozen balance for the same resource type, and overwrites the single stored `expireTime` for the *entire pooled balance* with the expiry of the new freeze. This is structurally identical to the VaderBond `deposit()` bug: calling the "deposit"/"freeze" entry point again for an account that already has an active, partially-matured lock silently resets/extends the unlock time for funds that were already close to becoming unlockable.

## Finding Description
`FreezeBalanceActuator.execute()` computes a single `expireTime = now + duration` for the incoming freeze request, then for BANDWIDTH/ENERGY calls: [1](#0-0) [2](#0-1) 

Both branches feed into `AccountCapsule.setFrozenForBandwidth` / `setFrozenForEnergy`, which unconditionally replace the account's single `Frozen` record (balance **and** `expireTime`) with the new combined balance and the freshly-computed `expireTime`, discarding whatever `expireTime` was previously stored: [3](#0-2) [4](#0-3) 

The same overwrite pattern exists in the TVM-native equivalent used by contract-triggered freezing: [5](#0-4) 

Because there is only one `Frozen`/`FrozenBalanceForEnergy` slot per account per resource type (enforced by the `frozenCount == 0 || frozenCount == 1` check in `validate()`), an account cannot have two independent unlock timers — any subsequent freeze call collapses the old and new balances into one record with one new expiry, effectively resetting the “vesting”/lock clock for balance that was already counting down toward unlock. This is confirmed by `UnfreezeBalanceActuator.validate()`, which gates unfreezing purely on the single stored `expireTime`: [6](#0-5) 

So if a user has 1,000 TRX frozen for ENERGY unlocking at `T1` (near future), and then freezes 1 additional TRX with a longer `frozenDuration`, the entire 1,001 TRX now becomes unlockable only at the later `T2`, exactly mirroring how `VaderBond.deposit()` resets the vesting term for previously-deposited, partially-vested payouts.

## Impact Explanation
A user (or dApp automating periodic small top-up freezes to maintain bandwidth/energy) can unintentionally push out the unlock time of their entire previously frozen balance, locking funds for far longer than originally committed. Because `frozenCount` is capped at 1, there is no way to freeze additional TRX without merging it into — and resetting the timer of — the existing lock. This causes unexpected fund illiquidity (an invalid-state/lock-duration divergence from user expectation), analogous to the acknowledged Vader finding, though it is self-inflicted rather than third-party-griefable.

## Likelihood Explanation
This is trivially reachable by any unprivileged account holder simply calling `FreezeBalanceContract` (or the TVM-native freeze) a second time while an unclaimed/soon-to-expire frozen balance exists — no special privileges or unusual state are required, and it is a normal, encouraged user action (freezing more TRX for bandwidth/energy).

## Recommendation
Either (a) disallow adding to an existing frozen balance without also allowing the previously-matured portion to be unfrozen first / carrying its original expiry forward per-tranche, or (b) explicitly document that additional freezes reset the unlock time for the pooled balance and surface this clearly in wallet/API tooling, consistent with how the Vader team handled the original finding (acknowledge and document rather than change vesting logic).

## Proof of Concept
1. Account A freezes 1,000 TRX for ENERGY with `frozenDuration = 3 days` → `expireTime = now + 3d` stored via `setFrozenForEnergy`.
2. Just before day 3, Account A freezes an additional 1 TRX for ENERGY with `frozenDuration = 30 days`.
3. `FreezeBalanceActuator.execute()` computes `newFrozenBalanceForEnergy = 1 TRX + 1000 TRX` and a new `expireTime = now + 30d`, then calls `accountCapsule.setFrozenForEnergy(1001 TRX, now + 30d)`, overwriting the previous 3-day expiry.
4. Calling `UnfreezeBalanceContract` for ENERGY at day 3 now fails validation (`frozenBalanceForEnergy.getExpireTime() > now`) because the whole 1001 TRX is locked until day 30, even though 1000 TRX had already matured.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L87-94)
```java
        } else {
          long oldNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          long newFrozenBalanceForBandwidth =
              frozenBalance + accountCapsule.getFrozenBalance();
          accountCapsule.setFrozenForBandwidth(newFrozenBalanceForBandwidth, expireTime);
          long newNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          increment = newNetWeight - oldNetWeight;
        }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L103-110)
```java
        } else {
          long oldEnergyWeight = accountCapsule.getEnergyFrozenBalance() / TRX_PRECISION;
          long newFrozenBalanceForEnergy =
              frozenBalance + accountCapsule.getEnergyFrozenBalance();
          accountCapsule.setFrozenForEnergy(newFrozenBalanceForEnergy, expireTime);
          long newEnergyWeight = accountCapsule.getEnergyFrozenBalance() / TRX_PRECISION;
          increment = newEnergyWeight - oldEnergyWeight;
        }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L1024-1041)
```java
  public void setFrozenForBandwidth(long frozenBalance, long expireTime) {
    Frozen newFrozen = Frozen.newBuilder()
        .setFrozenBalance(frozenBalance)
        .setExpireTime(expireTime)
        .build();

    long frozenCount = getFrozenCount();
    if (frozenCount == 0) {
      setInstance(getInstance().toBuilder()
          .addFrozen(newFrozen)
          .build());
    } else {
      setInstance(getInstance().toBuilder()
          .setFrozen(0, newFrozen)
          .build()
      );
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L1077-1089)
```java
  public void setFrozenForEnergy(long newFrozenBalanceForEnergy, long time) {
    Frozen newFrozenForEnergy = Frozen.newBuilder()
        .setFrozenBalance(newFrozenBalanceForEnergy)
        .setExpireTime(time)
        .build();

    AccountResource newAccountResource = getAccountResource().toBuilder()
        .setFrozenBalanceForEnergy(newFrozenForEnergy).build();

    this.account = this.account.toBuilder()
        .setAccountResource(newAccountResource)
        .build();
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L99-116)
```java
    } else { // acquire resource
      switch (param.getResourceType()) {
        case BANDWIDTH:
          accountCapsule.setFrozenForBandwidth(
              frozenBalance + accountCapsule.getFrozenBalance(),
              expireTime);
          break;
        case ENERGY:
          accountCapsule.setFrozenForEnergy(
              frozenBalance + accountCapsule.getAccountResource()
                  .getFrozenBalanceForEnergy()
                  .getFrozenBalance(),
              expireTime);
          break;
        default:
          logger.debug("Resource Code Error.");
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L448-456)
```java
        case ENERGY:
          Frozen frozenBalanceForEnergy = accountCapsule.getAccountResource()
              .getFrozenBalanceForEnergy();
          if (frozenBalanceForEnergy.getFrozenBalance() <= 0) {
            throw new ContractValidateException("no frozenBalance(Energy)");
          }
          if (frozenBalanceForEnergy.getExpireTime() > now) {
            throw new ContractValidateException("It's not time to unfreeze(Energy).");
          }
```
