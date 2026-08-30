### Title
Vault interest accrual silently freezes without reverting when paused, enabling retroactive-interest sniping of unclaimed yield - ([File: local-testing/contracts/vault/vault-usdc.clar])

### Summary
Every zVault's `accrue` function is guarded by a pause flag that, unlike every other pause flag in the same contract, does not revert the transaction when active. Instead, it silently returns the stale, pre-pause `index`/`lindex` values as a successful `(ok ...)` response. Because `deposit`, `redeem`, `system-borrow`, and `system-repay` all call `(try! (accrue))` and continue executing on whatever it returns, the entire pause window's interest is deferred rather than reverted, and `last-update` is never advanced while paused. When the pause is lifted, the very next `accrue()` call compounds the *entire* elapsed pause duration into a single step-function jump in the index. This creates a snipeable window: whoever deposits right before unpause or redeems right after unpause can capture (or avoid paying) the entire deferred interest in one shot, siphoning yield from other liquidity providers/debt holders who held their position throughout the paused period.

### Finding Description
`accrue()` in each vault contract (e.g. `vault-ststx.clar`, `vault-usdc.clar`, `vault-sbtc.clar`, and their `mainnet` `v0-*` counterparts) is defined as: [1](#0-0) 

```clarity
(define-public (accrue)
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
          (let ((next (next-index)) ...)
            (if (not (is-eq idx next)) (var-set index next) false)
            (if (not (is-eq lidx nliq)) (var-set lindex nliq) false)
            ...
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))
```

Every other pause flag in the same `pause-states` tuple (`deposit`, `redeem`) is enforced with an explicit `(asserts! (not (get deposit states)) ERR-PAUSED)` / `(asserts! (not (get redeem states)) ERR-PAUSED)` that reverts the call: [2](#0-1) 

```clarity
(define-public (deposit (amount uint) (min-out uint) (recipient principal))
    (let ((states (var-get pause-states))
          (u (try! (accrue)))
          ...)
    (asserts! (not (get deposit states)) ERR-PAUSED)
    ...
    (try! (receive-underlying amount account))
    (try! (ft-mint? zft inkind recipient))
    (var-set assets (+ current-assets amount))
    ...))

(define-public (redeem (amount uint) (min-out uint) (recipient principal))
  (let ((states (var-get pause-states))
        (u (try! (accrue)))
        ...)
  (asserts! (not (get redeem states)) ERR-PAUSED)
  ...
  (try! (ft-burn? zft amount account))
  (try! (send-underlying inkind recipient))
  ...))
```

`(try! (accrue))` succeeds unconditionally while `accrue` is paused (it returns `ok`, not an error), so `deposit`/`redeem` proceed to completion using the frozen `index`/`lindex` and the stale `convert-to-assets-preview`/`convert-to-shares-preview` conversion rates, exactly as if no time had passed. Crucially, the `last-update` timestamp is only advanced inside the "NOT PAUSED" branch, so while `accrue` is paused, `last-update` is frozen at its pre-pause value — the elapsed pause duration is not discarded, it is *deferred*.

The very first `accrue()` call after the pause is lifted computes `next-index()` from `stacks-block-time - last-update`, which now spans the *entire* pause window. All the interest that should have compounded gradually is applied in a single index jump at that instant.

### Impact Explanation
This matches the "pause that passes through instead of reverting" analog: the check exists (`get accrue states`) but instead of blocking state-changing operations during the paused/frozen period, it silently lets `deposit`/`redeem`/`system-borrow`/`system-repay` execute against a stale accounting state, and stores up the unaccounted interest for a single retroactive jump.

Because share price (`convert-to-assets`/`convert-to-shares`) is a function of `index`/`lindex`, a user who:
- deposits during the pause window or in the block immediately preceding unpause acquires shares at the pre-pause (undervalued) price, then benefits from the full retroactive interest jump the instant accrual resumes — capturing yield they never actually funded, diluting existing depositors' share of interest that accrued on the debt they were exposed to the whole time; or
- redeems immediately after the pause window closes but the underlying interest is applied to `debt`, exiting before their share of newly-materialized bad exposure/interest catches up to them at their expense of remaining LPs on the other side.

This is a theft-of-unclaimed-yield vector between the vault's ordinary liquidity providers and an attacker who times a deposit/withdraw around the pause boundary, which falls under the in-scope High-impact class: "theft of unclaimed yield ... or temporary freezing of funds."

### Likelihood Explanation
Exploitation requires only observing (or triggering, if a keeper/DAO proposal schedule is public/predictable) the `accrue` pause-state toggle and submitting a `deposit` or `redeem` transaction in the block adjacent to the unpause — no privileged access or DAO compromise is needed by the attacker, only knowledge of when the flag flips (which is emitted on-chain via the `set-pause-states` event). The mechanism itself is deterministic and reachable in a single transaction once the timing condition is met.

### Recommendation
Make `accrue`'s pause behavior consistent with the other flags in `pause-states`: either (a) revert with `ERR-PAUSED` when `accrue` is paused so that `deposit`/`redeem`/`system-borrow`/`system-repay` cannot execute against a stale index at all, or (b) if pausing accrual is intended to freeze interest permanently (not defer it), advance `last-update` to `stacks-block-time` even in the paused branch so that the paused duration is genuinely excluded from future interest calculations rather than compounded retroactively in one step.

### Proof of Concept
1. DAO/admin sets `pause-states.accrue = true` on `vault-usdc.clar` (e.g., for an emergency oracle/index freeze), while `deposit`/`redeem` remain unpaused.
2. Time passes; interest that should accrue on outstanding debt is not reflected in `index`/`lindex`, and `last-update` stays frozen at the pre-pause block time.
3. Attacker calls `deposit` on `vault-usdc.clar` right before the pause is lifted. `accrue()` returns the stale (pre-pause) `index`/`lindex` without reverting, so `deposit` mints shares at the stale (lower) share price.
4. DAO unpauses (`pause-states.accrue = false`).
5. Attacker (or anyone) triggers any vault call, causing `accrue()` to run its "NOT PAUSED" branch: `next-index()` computes the full elapsed pause duration in one jump, `index`/`lindex` spike, and `last-update` resets to now.
6. Attacker calls `redeem` immediately, cashing out shares at the newly inflated share price — capturing the entire deferred interest windfall for the paused duration despite having deposited only moments before the jump, at the expense of LPs who held shares throughout the actual paused period.

### Citations

**File:** local-testing/contracts/vault/vault-ststx.clar (L837-867)
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

**File:** local-testing/contracts/vault/vault-sbtc.clar (L761-833)
```text
    (ok true)))

;; -- Vault operations -------------------------------------------------------

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

  (try! (ft-burn? zft amount account))
  (try! (send-underlying inkind recipient))
  (var-set assets (- current-assets inkind))

  (print {
    action: "redeem",
    caller: contract-caller,
    data: {
      redeemer: account,
      recipient: recipient,
      shares-burned: amount,
      amount-received: inkind,
      assets: (- current-assets inkind)
    }
  })

  (ok inkind)))
```
