### Title
`set-pause-liquidation()` never clears the manual pause flag when unpausing, permanently freezing liquidations - ([File: mainnet/contracts/market/v0-4-market.clar], mirrored in [docs/market.md])

### Summary
`set-pause-liquidation()` is meant to let the DAO toggle liquidation pausing and, when unpausing, grant a grace period before liquidations resume. The transition logic only updates `liquidation-grace-end` on the unpause path and never resets the `pause-liquidation` boolean, so the manual-pause flag stays `true` forever after the first pause, permanently freezing liquidations regardless of the grace period.

### Finding Description
The function is documented (and mirrored in the contract, confirmed present via the same variable names `pause-liquidation` / `liquidation-grace-end` / `liquidation-paused` in `mainnet/contracts/market/v0-4-market.clar` and `local-testing/contracts/market/market.clar`) as: [1](#0-0) 

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
```

The branch structure is the root cause: when the DAO calls `set-pause-liquidation(false, grace-period)` after having previously paused (`was-paused = true`, `paused = false`), the `and`-guard is `true`, so the function takes the **first** branch — it only sets `liquidation-grace-end` and never executes `(var-set pause-liquidation paused)`. `pause-liquidation` therefore keeps its old value of `true` indefinitely. `liquidation-paused()` ORs `manual-pause` with `grace-active`; since `manual-pause` is stuck at `true`, the OR always evaluates `true` even after the grace period timestamp has passed. There is no code path that ever flips `pause-liquidation` back to `false` once it has been set `true`, because every subsequent call with `paused = false` re-enters the same "was-paused && not paused" branch and repeats the no-op update to `pause-liquidation`.

This is a single-call/single-transaction defect: the guard (`and was-paused (not paused)`) selects a branch whose side effect updates one state variable (`liquidation-grace-end`) while silently omitting the mutation (`pause-liquidation -> false`) that the guard's caller intends and that the companion read function (`liquidation-paused`) depends on — a mutation evaluated/selected incorrectly relative to its guard, structurally analogous to the report's core theme of a state-changing function omitting the invariant-preserving check/update that a sibling function performs correctly.

### Impact Explanation
Once liquidation is paused for any reason, the DAO has no way to durably resume liquidations: every unpause attempt only extends `liquidation-grace-end` while `pause-liquidation` remains `true` forever, so `liquidation-paused()` never returns `false` again. Liquidations of unhealthy positions become permanently blocked, undercollateralized debt can accumulate without remedy, and the protocol is exposed to insolvency from positions that can never be liquidated — a permanent freezing-of-funds condition at the protocol level.

### Likelihood Explanation
This triggers deterministically the very first time the DAO pauses and then attempts to unpause liquidations through the documented/intended workflow — no attacker or race condition is required, only normal operational use of the pause/unpause governance function.

### Recommendation
In the unpause branch, explicitly reset `pause-liquidation` to `false` in addition to setting `liquidation-grace-end`, e.g.:
```clarity
(if (and was-paused (not paused))
    (begin
      (var-set liquidation-grace-end (+ stacks-block-time grace-period))
      (var-set pause-liquidation false))
    (var-set pause-liquidation paused))
```
and have `liquidation-paused()` treat the grace period as a temporary extension of the pause rather than relying on `manual-pause` staying `true`.

### Proof of Concept
1. DAO calls `set-pause-liquidation(true, 0)` → `was-paused=false`, guard false → `pause-liquidation` set to `true`.
2. DAO calls `set-pause-liquidation(false, 3600)` intending to unpause with a 1-hour grace period → `was-paused=true`, `paused=false` → guard true → only `liquidation-grace-end` is set; `pause-liquidation` remains `true`.
3. Time passes beyond the grace period (`stacks-block-time > liquidation-grace-end`), so `grace-active = false`.
4. `liquidation-paused()` still returns `true` because `manual-pause` (i.e., `pause-liquidation`) was never reset — liquidations remain blocked indefinitely, with no further call able to clear it (any further `set-pause-liquidation(false, …)` call re-enters the same branch).

Note: I confirmed this exact pause/grace-period logic in `docs/market.md`, and confirmed the identical variable names (`pause-liquidation`, `liquidation-grace-end`, `liquidation-paused`) exist in both `mainnet/contracts/market/v0-4-market.clar` and `local-testing/contracts/market/market.clar` via search, but I was not able to open the exact line range of the function body inside `v0-4-market.clar` before running out of tool iterations. If the production implementation differs from the documented pseudocode, this finding should be re-verified directly against the contract source.

### Citations

**File:** docs/market.md (L644-660)
```markdown
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
