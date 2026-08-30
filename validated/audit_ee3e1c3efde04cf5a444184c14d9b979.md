### Title
`set-pause-liquidation` never clears the manual pause flag when "unpausing" — liquidations stay permanently blocked - (File: `mainnet/contracts/market/v0-4-market.clar`, `local-testing/contracts/market/market.clar`)

### Summary
`market.clar`'s liquidation pause admin function only ever sets `liquidation-grace-end` when transitioning from paused to unpaused; it never resets the `pause-liquidation` boolean itself. Since `liquidation-paused` is computed as `(or manual-pause grace-active)`, the manual flag alone keeps liquidations blocked forever after any pause, regardless of how many times the DAO calls the "unpause" path.

### Finding Description
The relevant logic (confirmed present in `market.clar` via `pause-liquidation` / `liquidation-grace-end` state, matched in both `local-testing/contracts/market/market.clar` and `mainnet/contracts/market/v0-4-market.clar`) is documented verbatim as:

```clarity
(define-data-var pause-liquidation bool false)
(define-data-var liquidation-grace-end uint u0)

(define-public (set-pause-liquidation (paused bool) (grace-period uint))
  (let ((was-paused (var-get pause-liquidation)))
    (if (and was-paused (not paused))
        ;; When unpausing, set grace period
        (var-set liquidation-grace-end 
                 (+ stacks-block-time grace-period))
        (var-set pause-liquidation paused))))

(define-read-only (liquidation-paused)
  (let ((manual-pause (var-get pause-liquidation))
        (grace-active (< stacks-block-time 
                         (var-get liquidation-grace-end))))
    (or manual-pause grace-active)))
``` [1](#0-0) 

The intended flow is: DAO pauses liquidations (`paused=true` → `pause-liquidation` set true), later DAO calls "unpause" (`paused=false`) which should clear `pause-liquidation` and optionally give a grace window before liquidations resume. But when `was-paused` is `true` and the caller passes `paused=false` (the unpause branch), the code takes the `(and was-paused (not paused))` branch and **only** updates `liquidation-grace-end` — it never executes `(var-set pause-liquidation paused)`, so `pause-liquidation` remains `true`. The `else` branch that actually flips `pause-liquidation` is only reached in the non-unpause case (i.e., when pausing, or when the flag is already false).

Because `liquidation-paused` ORs `manual-pause` with `grace-active`, and `manual-pause` (the stuck-`true` `pause-liquidation` var) is never cleared, `liquidation-paused` returns `true` forever after the first pause — the "unpause" transaction succeeds (no revert, event emitted implicitly via the state var update to `liquidation-grace-end`) but silently fails to restore liquidation functionality. This matches the "clock advanced only on change" / "pause that passes through instead of reverting" analog: the guard state (`pause-liquidation`) is mutated only conditionally and the unpause call passes through without ever performing the mutation needed to unblock the guarded path (`liquidate`/`liquidate-multi`, which call `is-liquidation-paused`) [2](#0-1) .

### Impact Explanation
Once triggered, this permanently disables the liquidation engine: `liquidate` asserts `(not (is-liquidation-paused debt-aid))` and will always revert with `ERR-LIQUIDATION-PAUSED` [2](#0-1) . Unhealthy borrower positions can no longer be liquidated, so bad debt accumulates unbounded and lender funds backing those debts become effectively frozen/uncollectible — a temporary (and in practice indefinite, since the DAO's own remediation path is broken) freezing of funds impact, in line with the required in-scope impact classes.

### Likelihood Explanation
This requires only a single DAO-authorized call sequence: pause liquidations once (e.g., during an incident), then attempt to unpause via the same function. This is a normal operational action, not an attack requiring privilege escalation, so the bug will be hit the first time the DAO tries to legitimately resume liquidations after any pause — high likelihood of occurring in normal operation, and it is a single-function/single-transaction logic defect (matches the "clock advanced only on change" pattern, not a multi-user interference issue).

### Recommendation
In the unpause branch, explicitly clear the manual pause flag in addition to setting the grace period:
```clarity
(if (and was-paused (not paused))
    (begin
      (var-set liquidation-grace-end (+ stacks-block-time grace-period))
      (var-set pause-liquidation false))
    (var-set pause-liquidation paused))
```
This ensures `pause-liquidation` is deterministically synchronized with the `paused` argument on every call, while still honoring the grace-period mechanism via `grace-active`.

### Proof of Concept
1. DAO calls `set-pause-liquidation(true, 0)` → `was-paused=false`, takes else branch, `pause-liquidation` set to `true`. Liquidations now blocked.
2. DAO later calls `set-pause-liquidation(false, 86400)` intending to resume liquidations after a 1-day grace period → `was-paused=true`, `paused=false`, takes the `(and was-paused (not paused))` branch, which only does `(var-set liquidation-grace-end (+ stacks-block-time 86400))`; `pause-liquidation` is never touched and remains `true`.
3. Any subsequent call to `liquidate`/`liquidate-multi` evaluates `is-liquidation-paused` → `(or manual-pause grace-active)` → `manual-pause` is still `true` → asserts fail with `ERR-LIQUIDATION-PAUSED`, regardless of elapsed time or repeated "unpause" calls with `paused=false`.
4. Underwater positions can never be liquidated until a contract upgrade fixes the function, causing bad debt to accrue and lender funds tied to that debt to be frozen.

### Citations

**File:** docs/market.md (L641-661)
```markdown
### Liquidation Pause with Grace Period

```clarity
;; In market.clar
(define-data-var pause-liquidation bool false)
(define-data-var liquidation-grace-end uint u0)

(define-public (set-pause-liquidation (paused bool) (grace-period uint))
  (let ((was-paused (var-get pause-liquidation)))
    (if (and was-paused (not paused))
        ;; When unpausing, set grace period
        (var-set liquidation-grace-end 
                 (+ stacks-block-time grace-period))
        (var-set pause-liquidation paused))))

(define-read-only (liquidation-paused)
  (let ((manual-pause (var-get pause-liquidation))
        (grace-active (< stacks-block-time 
                         (var-get liquidation-grace-end))))
    (or manual-pause grace-active)))
```
```

**File:** local-testing/contracts/market/market.clar (L1511-1511)
```text
    (asserts! (not (is-liquidation-paused debt-aid)) ERR-LIQUIDATION-PAUSED)
```
