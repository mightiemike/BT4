### Title
Vault `accrue` pause silently pass-throughs instead of reverting, freezing `last-update` and causing retroactive interest to be misattributed to depositors who enter during the pause window - ([File: mainnet/contracts/vault/v0-vault-stx.clar])

### Summary
Every vault contract (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`, and their `local-testing` counterparts) implements `accrue` with a pause branch that does not revert on `ERR-PAUSED` — instead it silently returns the stale `index`/`lindex` as `(ok ...)` and skips updating `last-update`. Every state-changing entrypoint (`deposit`, `redeem`, `system-borrow`, `system-repay`, `transfer`) unconditionally calls `(try! (accrue))` expecting either a fresh accrual or a hard revert, but instead gets a "successful" stale result and proceeds to execute against real (non-reverted) balances. This mirrors the `proveBlocks` defect in the external report: a guard/verification step exists in the source ("check the state before proceeding") but is effectively disconnected from the operation that depends on it, because the pass-through swallows the invalidating condition rather than propagating it.

### Finding Description
`accrue` in every vault (e.g. `mainnet/contracts/vault/v0-vault-stx.clar`, lines 833–863):
```clarity
(define-public (accrue)
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
          (let ((next (next-index)) (nliq (next-liquidity-index)) ...)
            ...
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))
``` [1](#0-0) 

`last-update` (the clock used by `next-index`/`next-liquidity-index` to compute elapsed-time interest) is only advanced inside the "NOT PAUSED" branch, and only when the index actually changed. This is the "clock advanced only on change" analog: while `pause-states.accrue` is `true`, `last-update` is frozen, but real chain time keeps advancing. `deposit` calls `(u (try! (accrue)))` and continues on the `(ok ...)` result:
```clarity
(define-public (deposit (amount uint) (min-out uint) (recipient principal))
    (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
      ...
      (inkind (convert-to-shares-preview amount)))
    ...
    (try! (receive-underlying amount account))
    (try! (ft-mint? zft inkind recipient))
    (var-set assets (+ current-assets amount))
``` [2](#0-1) 

Because `try!` only short-circuits on an `err`, and the paused branch always returns `ok`, `deposit`/`redeem`/`system-borrow`/`system-repay`/`transfer` never see a failure and continue to mutate `assets`, mint/burn `zft` shares, and move funds using the un-advanced (stale) `index`/`lindex` — exactly the "pause that passes through instead of reverting" pattern called out as in-scope, and the "clock advanced only on change" pattern combined.

When the DAO later flips `pause-states.accrue` back to `false`, the very next call recomputes `next-index`/`next-liquidity-index` using elapsed time from the old, frozen `last-update` — i.e., the *entire pause duration plus any subsequent delay* is compounded into interest in a single jump, rather than being excluded as the pause presumably intended. Any depositor who supplied liquidity during the pause window (at the stale, pre-jump `index`/`lindex`) receives shares priced off `convert-to-shares-preview`, computed from `total_supply`/`total_assets` that have not yet reflected the retroactive interest. When the burst is finally applied, that retroactive interest — which was actually earned entirely before the new depositor's funds were in the pool — is distributed pro-rata across *all* current `zft` holders, including the depositor who joined mid-pause. This dilutes the yield rightfully owed to holders who supplied before the pause and transfers part of it to the late depositor.

### Impact Explanation
This is a theft/misallocation of unclaimed yield (High): suppliers present before the pause have their accrued-but-unrealized interest diluted by new deposits admitted during the pause window, and the diverted yield lands with whoever deposits right before accrual resumes. Because `deposit`, `redeem`, `system-borrow`, and `system-repay` are all reachable through `market.clar`'s `collateral-add`/`borrow`/`repay`/`supply-collateral-add` flows (e.g. `vault-deposit`, `vault-system-borrow`, `accrue-and-cache`), this is reachable via the normal user-facing lending flows without any privileged access, purely by depositing while the vault's `accrue` pause flag is enabled and withdrawing/holding through the unpause event.

### Likelihood Explanation
Requires the DAO to have set `pause-states.accrue = true` at some point (a normal operational action, e.g. during an incident response) and a user to deposit during that window, then wait for unpause. This is not attacker-privileged and does not require DAO compromise — the DAO's use of the pause flag is expected/intended governance behavior; the bug is that the underlying accounting mechanism (index/`last-update` freeze without corresponding time exclusion) causes an unintended cross-subsidy once accrual resumes. Likelihood is moderate: it depends on an operational pause being used, which is plausible during oracle incidents, upgrades, or emergency responses documented elsewhere in the codebase (`docs/vaults.md`, pausability sections).

### Recommendation
When `accrue` is paused, either (a) revert the whole pause branch so dependent operations (`deposit`/`redeem`/`system-borrow`/`system-repay`) cannot proceed while indices are stale, or (b) still advance `last-update` to `stacks-block-time` on every call (paused or not) so that once unpaused, the elapsed-time calculation excludes the paused interval and no retroactive interest burst occurs. Additionally, gate `deposit`/`redeem` explicitly on the `accrue` pause state, not just their own state flag, so stale-index deposits cannot be created in the first place.

### Proof of Concept
1. DAO calls `set-pause-states` on `v0-vault-stx` with `accrue: true` (a normal, permitted governance action). [3](#0-2) 
2. Time passes (real chain time advances, `last-update` remains frozen because the "NOT PAUSED" branch — the only place `var-set last-update` occurs — is never executed).
3. During this window, Alice calls `deposit` on `v0-vault-stx`; `(try! (accrue))` returns `(ok {index: idx, lindex: lidx})` (stale), so `deposit` proceeds and mints Alice `zft` shares at the frozen, un-accrued exchange rate. [2](#0-1) 
4. DAO calls `set-pause-states` again with `accrue: false`.
5. The next call to `accrue` (triggered by any subsequent `deposit`/`redeem`/`borrow`/`repay`) computes `next-index`/`next-liquidity-index` using the full elapsed time since the original (pre-pause) `last-update`, retroactively compounding interest for the entire pause duration in one step and finally updating `last-update`. [4](#0-3) 
6. Alice's shares — minted during the pause at the pre-burst rate — now participate in redeeming against the post-burst `lindex`, capturing a portion of interest that accrued entirely before her deposit, diluting the yield of suppliers who held shares through the entire period.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L763-795)
```text
(define-public (deposit (amount uint) (min-out uint) (recipient principal))
    (let (
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
    
    (print {
      action: "deposit",
      caller: contract-caller,
      data: {
        depositor: account,
        recipient: recipient,
        amount: amount,
        shares-minted: inkind,
        assets: (+ current-assets amount)
      }
    })

    (ok inkind)))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L833-863)
```text
;; -- Lending operations -----------------------------------------------------

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
