### Title
`accrue` Pause Passes Through Instead of Reverting, Letting the DAO Freeze Interest Accrual While Users Keep Redeeming at a Stale Index - ([File: mainnet/contracts/vault/v0-vault-sbtc.clar])

### Summary
The external report's root bug class is "a pause that passes through instead of reverting" causing withdrawals/state updates to silently no-op while other flows continue, letting a privileged actor extract value at users' expense. The same pattern exists in Zest's yield vaults (`v0-vault-sbtc.clar`, `v0-vault-stx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`): `accrue` silently returns `ok` with the stale `index`/`lindex` when `accrue` is paused, instead of reverting, while `deposit`/`redeem`/`system-borrow`/`system-repay` are gated by *separate* pause flags and keep executing against that stale index.

### Finding Description
`accrue` checks its own pause flag and, when paused, takes a "pass-through" branch that returns the current (unmodified) index/lindex without reverting and without updating `last-update`: [1](#0-0) 

Every other state-changing entry point (`deposit`, `redeem`, `system-borrow`, `system-repay`) unconditionally calls `(try! (accrue))` first and then checks *its own* pause bit (`deposit`, `redeem`, `borrow`, `repay`) — not the `accrue` bit: [2](#0-1) [3](#0-2) 

This is the same "value bound before its invalidating event, used after" shape as the report: the index/lindex are cached in `index`/`lindex` vars; the invalidating event is the passage of block-time while `accrue` is paused; and the later use is `convert-to-assets-preview`/`convert-to-shares-preview` inside `redeem`/`deposit`, which still execute (since `redeem`/`deposit` are independently un-paused) using the frozen conversion rate instead of reverting.

`set-pause-states` itself acknowledges this asymmetry — it force-calls `accrue()` right before pausing accrual to "capture pending interest", and on unpause jumps `last-update` forward to "skip the paused period": [4](#0-3) 
This confirms the intended invariant — interest owed should be frozen exactly at the pause boundary — but nothing stops the DAO from pausing only `accrue` while leaving `deposit`/`redeem` open, so users continue trading shares against a rate that no longer reflects real yield/debt growth for as long as the pause lasts.

### Impact Explanation
While `accrue` is paused but `redeem`/`deposit` remain open, `total-assets-preview`/`convert-to-assets-preview` no longer reflect interest that is actually still accruing to borrowers off-chain-equivalent (borrower debt via `system-borrow`/`system-repay` is likewise mispriced against the frozen index). Depositors redeeming during this window receive a share price that undercounts real vault yield, and the treasury `reserve-inc` fee mint (line 847-856) is skipped entirely for the paused duration, permanently losing that fee income. This is a temporary/permanent freezing/misallocation of unclaimed yield to legitimate LPs and the treasury — an in-scope **High** impact (theft/freezing of unclaimed yield).

### Likelihood Explanation
The DAO (`check-dao-auth`) can call `set-pause-states` with `accrue: true` while leaving `deposit`/`redeem`/`borrow`/`repay` false in a single transaction; this requires no external compromise, just the same DAO-controlled toggle the protocol already uses for pausing, so it is straightforwardly reachable by the party the report describes as adversarial (a privileged pauser acting against user interest).

### Recommendation
Make `accrue`'s pause consistent with the other flags: either (a) have `deposit`/`redeem`/`borrow`/`repay` also assert `(not (get accrue states))` before proceeding, so that a paused accrual halts all flows that depend on the index (mirroring the report's recommendation to never let a "pass-through" bypass leave user-facing state inconsistent), or (b) remove the separate `accrue` pause bit and only allow pausing pause-states as a set so `index`/`lindex` cannot be used stale.

### Proof of Concept
1. DAO calls `set-pause-states` with `{accrue: true, deposit: false, redeem: false, borrow: false, repay: false, flashloan: false}`. This force-runs `accrue()` once, capturing interest to the pause boundary [5](#0-4) .
2. Time passes (block-time advances); borrower debt would normally have grown via `next-index`, but `accrue` is now short-circuited to pass through the stale `index`/`lindex` every time it's invoked [6](#0-5) .
3. Users continue calling `redeem`, which still calls `(try! (accrue))` (no-op) and then computes `convert-to-assets-preview` off the frozen `index`/`lindex`, and passes its own `redeem` pause check (still false) [2](#0-1) , extracting assets at a stale conversion rate instead of the pause blocking the operation outright.
4. Any `reserve-inc`/treasury fee mint that would have occurred during that window is permanently lost, and the DAO later unpauses by jumping `last-update` forward, discarding the entire paused period's real economic activity [7](#0-6) .

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L721-748)
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

;; -- Token operations -------------------------------------------------------
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L763-780)
```text
      (states (var-get pause-states))
      (u (try! (accrue)))
      (account contract-caller)
      (CAP-SUPPLY (var-get cap-supply))
      (current-assets (var-get assets))
      (inkind (convert-to-shares-preview amount)))

    (asserts! (not (get deposit states)) ERR-PAUSED)
    (asserts! (var-get initialized) ERR-INIT)
    (asserts! (not (var-get in-flashloan)) ERR-REENTRANCY)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
    (asserts! (>= inkind min-out) ERR-SLIPPAGE)
    (asserts! (<= (+ current-assets amount) CAP-SUPPLY) ERR-SUPPLY-CAP-EXCEEDED)

    (try! (receive-underlying amount account))
    (try! (ft-mint? zft inkind recipient))
    (var-set assets (+ current-assets amount))

```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L795-811)
```text
(define-public (redeem (amount uint) (min-out uint) (recipient principal))
  (let (
    (states (var-get pause-states))
    (u (try! (accrue)))
    (account contract-caller)
    (current-assets (var-get assets))
    (balance (get-balance-internal account))
    (balance-check (asserts! (>= balance amount) ERR-INSUFFICIENT-BALANCE))
    (available-assets (get-available-assets))
    (inkind (convert-to-assets-preview amount)))

  (asserts! (>= current-assets inkind) ERR-INSUFFICIENT-ASSETS)
  (asserts! (not (get redeem states)) ERR-PAUSED)
  (asserts! (> amount u0) ERR-AMOUNT-ZERO)
  (asserts! (> inkind u0) ERR-OUTPUT-ZERO)
  (asserts! (>= inkind min-out) ERR-SLIPPAGE)
  (asserts! (>= available-assets inkind) ERR-INSUFFICIENT-LIQUIDITY)
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L833-861)
```text
(define-public (accrue)
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
          (let ((next (next-index))
                (nliq (next-liquidity-index))
                (scaled-principal (var-get principal-scaled))
                (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
                (new-debt (mul-div-down scaled-principal next INDEX-PRECISION))
                (debt-delta (if (> new-debt old-debt) (- new-debt old-debt) u0))
                (reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
                (treasury-lp (if (> reserve-inc u0) (mul-div-down reserve-inc (total-supply) (- (total-assets-preview) reserve-inc)) u0)))
            (if (not (is-eq idx next))
                (var-set index next)
                false)
            (if (not (is-eq lidx nliq))
                (var-set lindex nliq)
                false)
            (if (> treasury-lp u0)
                (try! (ft-mint? zft treasury-lp .dao-treasury))
                false)
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))
```
