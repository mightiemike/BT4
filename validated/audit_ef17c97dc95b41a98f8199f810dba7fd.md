### Title
Accrue-pause pass-through lets deposits/redeems mint or burn shares at a stale, un-accrued index while frozen interest is silently skipped on unpause - (File: mainnet/contracts/vault/v0-vault-stx.clar)

### Summary
`accrue` in `v0-vault-stx.clar` implements a pause branch that returns the *current* (potentially stale) `index`/`lindex` pair without recomputing accrual, instead of reverting the call: [1](#0-0) 

`deposit` and `redeem` both unconditionally call `(try! (accrue))` and then use `convert-to-shares-preview` / `convert-to-assets-preview`, whose share/asset math depends on `index`/`lindex`: [2](#0-1) 

Because `deposit`/`redeem` have their *own* independent pause flags (`get deposit states`, `get redeem states`) that are unrelated to the `accrue` pause flag, an admin can pause `accrue` while leaving `deposit`/`redeem` open. `set-pause-states` only accrues once, at the moment `accrue` transitions from unpaused→paused, and jumps `last-update` forward only when it transitions paused→unpaused: [3](#0-2) 

### Finding Description
1. `set-pause-states` sets `accrue: true` (pausing it) while leaving `deposit`/`redeem` unpaused, having accrued once to "capture pending interest."
2. For the remainder of the paused period (which can span many blocks/transactions), every call to `deposit` or `redeem` invokes `accrue`, which hits the pause branch and returns the frozen `{index, lindex}` without ever calling `next-index`/`next-liquidity-index` and without updating `last-update`.
3. Users can continue to `deposit` and `redeem` throughout this window. Because the index used for `convert-to-shares-preview`/`convert-to-assets-preview` never advances, real economic value (accrued interest that would otherwise have been earned by suppliers and owed by borrowers, plus the DAO's reserve-factor mint of `treasury-lp`) is permanently skipped for the whole pause duration - it is not merely delayed, it never materializes because `last-update` is jumped straight to "now" on unpause:
`(var-set last-update stacks-block-time)` - discarding the entire paused interval's accrual rather than catching it up.
4. This is the "pause that passes through instead of reverting" pattern called out in the rules: instead of blocking state-changing operations that depend on a fresh index, `accrue` silently returns a stale value and lets `deposit`/`redeem` proceed on it.

### Impact Explanation
Any deposit or redeem executed while `accrue` is paused (but `deposit`/`redeem` are not) mints/burns shares using an index that will never be corrected for the elapsed time - the protocol design explicitly discards, rather than defers, interest that accrued during the pause window. This is a permanent freezing/loss of unclaimed yield: suppliers who hold shares through the pause window lose interest they would otherwise have earned (temporary freezing of funds if the position is later normalized; permanent loss of yield if never caught up, which is exactly what the unpause logic does by jumping `last-update` forward). The DAO treasury's reserve-factor share (`treasury-lp`, minted only inside the non-paused branch) is likewise permanently lost for that window. This lands in the in-scope impact category of "theft/permanent freezing of unclaimed yield or royalties."

### Likelihood Explanation
This requires only a single privileged pause-configuration action (`set-pause-states` toggling `accrue: true` while leaving `deposit`/`redeem: false`) followed by ordinary user deposits/redeems during the pause window - no collusion between two independent users, no oracle manipulation, and no DAO-registry misconfiguration is needed; it is a direct consequence of the pause pass-through design combined with independent per-action pause flags. Likelihood depends entirely on operational use of partial pause configurations, which the contract explicitly allows (pause flags are set per-action, not coupled).

### Recommendation
Either (a) make `deposit`/`redeem` (and any other state-mutating entry point) revert when `accrue` is paused, rather than silently proceeding on a stale index, or (b) when unpausing `accrue`, catch up the skipped interval by continuing normal accrual math from `last-update` instead of jumping `last-update` straight to `stacks-block-time`, so no accrual window is silently discarded.

### Proof of Concept
1. DAO/admin calls `set-pause-states` with `{ deposit: false, redeem: false, borrow: false, repay: false, accrue: true, flashloan: false }`. This captures pending interest once via the pre-pause `(try! (accrue))` call, then sets `pause-states` to the new config.
2. Time passes (many blocks), during which suppliers continue to call `deposit` and other users call `redeem`. Each call's internal `(try! (accrue))` hits the `PAUSED` branch and returns the unchanged `{index, lindex}` - no interest accrues, no `treasury-lp` is minted, `last-update` stays frozen at its pre-pause value.
3. Admin calls `set-pause-states` again with `accrue: false`. Because `was-paused` is true and `now-paused` is false, the code executes `(var-set last-update stacks-block-time)`, jumping the clock straight to "now" and permanently skipping accrual for the entire paused interval - the interest for that window is never charged to borrowers or credited to suppliers/treasury.
4. Result: all shares minted/burned via `deposit`/`redeem` during the pause window were valued at a stale index, and the interest for the whole pause duration is unrecoverably lost rather than deferred, causing a permanent loss of unclaimed yield for suppliers and the DAO treasury.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L723-748)
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

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L763-831)
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

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L835-863)
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
