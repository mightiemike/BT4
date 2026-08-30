### Title
Unpausing `accrue` jumps `last-update` forward without accruing the paused period's implied interest, silently discounting borrower debt/supplier yield - ([File: mainnet/contracts/vault/v0-vault-usdc.clar])

### Summary
The vault's `set-pause-states` function, used to pause/unpause vault operations, contains special-case handling for the `accrue` flag: when accrue transitions from paused to unpaused, `last-update` is force-set to `stacks-block-time` instead of being advanced through the normal interest-accrual math, silently discarding the entire paused interval from the interest calculation.

### Finding Description
`set-pause-states` in the vault contracts (e.g. `mainnet/contracts/vault/v0-vault-usdc.clar`, and identically in `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-stx.clar`, `v0-vault-usdh.clar`) reads:

```
(define-public (set-pause-states (states {deposit: bool, redeem: bool, borrow: bool, repay: bool, accrue: bool, flashloan: bool}))
  (begin
    (try! (check-dao-auth))
    (let ((current (var-get pause-states))
          (was-paused (get accrue current))
          (now-paused (get accrue states)))
      ;; When pausing accrue, accrue first to capture pending interest
      (if (and (not was-paused) now-paused)
          (begin (try! (accrue)) false)
          false)
      ;; When unpausing accrue, jump last-update to now to skip paused period
      (if (and was-paused (not now-paused))
          (var-set last-update stacks-block-time)
          false)
      (var-set pause-states states)
      ...
      (ok true))))
``` [1](#0-0) 

`last-update` is the timestamp used by `accrue` to compute the elapsed time since the last index update and scale the interest applied to `index`/`lindex` (borrow/supply indices) per unit time, as seen in the `accrue` function which reads `last-update` to determine elapsed seconds for interest computation [2](#0-1) . When the DAO pauses `accrue`, the code correctly calls `accrue` once beforehand to capture interest up to the pause point. However, when unpausing, instead of leaving `last-update` untouched (which would cause the next `accrue` call to naturally include the paused duration) or applying the same rate retroactively, the code forcibly sets `last-update` to the current `stacks-block-time`. This deliberately zeroes out the elapsed-time delta for the entire paused interval, meaning no interest accrues for that period at all — this is the "clock advanced only on change" pattern: the accrual clock is stepped forward as a side effect of the pause-state toggle transaction rather than being derived from real elapsed blocks/time through the normal accrual path.

### Impact Explanation
This causes permanent freezing/loss of unclaimed yield: suppliers who are owed interest for the paused period never receive it because the index is never advanced for that interval, and borrowers who owe interest for the paused period are permanently relieved of it. Since the DAO can pause/unpause `accrue` at will (and pausing/unpausing is a normal, expected multisig operation, not a compromise), any such cycle deterministically strands the paused-period's yield — this lands squarely in the "temporary/permanent freezing of unclaimed yield" impact class. The value lost is proportional to `(pause-duration × active borrow rate × total-debt)`, which can be made arbitrarily large by an admin action that is not otherwise malicious (e.g., a legitimate pause for maintenance still discards the yield accrued during downtime, and if pause/unpause is invoked repeatedly the cumulative discarding of interest compounds).

### Likelihood Explanation
This is a single-transaction, deterministic outcome — it requires only that the DAO executor call `set-pause-states` twice (once to pause `accrue`, once to unpause it) with any transaction/block gap between the two. It does not depend on any external attacker, oracle behavior, race condition between separate users, or privileged-key compromise; the DAO's normal, intended pause/unpause maintenance flow itself triggers the loss. Because pausing for maintenance/upgrades is a documented and expected operational action, the bug is highly likely to be triggered during ordinary protocol operation.

### Recommendation
Do not forcibly reset `last-update` to `stacks-block-time` on unpause. Instead, either (a) leave `last-update` unchanged so that the next `accrue` call correctly measures the full elapsed period (assuming the intent is for interest to still accrue through the pause, since capital is still locked/at risk), or (b) if the intent is genuinely to exclude the paused window from interest accrual, explicitly document this as intended behavior and ensure it is symmetric and audited, since it otherwise silently changes the economic terms of every outstanding position without notice to users.

### Proof of Concept
1. DAO calls `set-pause-states` with `accrue: true` (and other flags unchanged) → `was-paused = false`, `now-paused = true` → contract calls `accrue` to snapshot interest up to now, then sets `pause-states` to paused.
2. Time passes (e.g., 30 days) while `accrue` is paused; outstanding debt continues to be owed by borrowers at the pre-pause rate conceptually, but no interest is recorded since `accrue` is blocked.
3. DAO calls `set-pause-states` with `accrue: false` → `was-paused = true`, `now-paused = false` → contract executes `(var-set last-update stacks-block-time)`, resetting the clock to "now" instead of the pre-pause timestamp.
4. Any subsequent call to `accrue` (via deposit/redeem/borrow/repay) computes elapsed time as `stacks-block-time - last-update ≈ 0`, so the entire 30-day interest window is never applied to `index`/`lindex`.
5. Suppliers permanently lose the yield they should have earned over the 30 days, and borrowers permanently avoid the interest they should have owed — a permanent freezing/loss of unclaimed yield triggered by a normal two-transaction admin sequence. [1](#0-0)

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L721-746)
```text
(define-public (set-pause-states (states {deposit: bool, redeem: bool, borrow: bool, repay: bool, accrue: bool, flashloan: bool}))
  (begin
    (try! (check-dao-auth))
    (let ((current (var-get pause-states))
          (was-paused (get accrue current))
          (now-paused (get accrue states)))
      ;; When pausing accrue, accrue first to capture pending interest
      (if (and (not was-paused) now-paused)
          (begin (try! (accrue)) false)
          false)
      ;; When unpausing accrue, jump last-update to now to skip paused period
      (if (and was-paused (not now-paused))
          (var-set last-update stacks-block-time)
          false)
      (var-set pause-states states)
      
      (print {
        action: "vault-set-pause-states",
        caller: tx-sender,
        data: {
          vault: UNDERLYING,
          states: states
        }
      })
      
      (ok true))))
```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L837-840)
```text
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
```
