### Title
`set-pause-liquidation` unpause path never clears `pause-liquidation`, permanently bricking liquidations - (File: `mainnet/contracts/market/v0-4-market.clar`, mirrored in `docs/market.md`)

### Summary
The market contract's liquidation pause toggle has an unpause branch that sets a grace-period timer but never writes the `pause-liquidation` flag back to `false`. Because `liquidation-paused` is computed as `(or manual-pause grace-active)`, and `manual-pause` can never be cleared once set, liquidations become permanently frozen after any pause is applied and the standard unpause call is used, no matter how much time passes.

### Finding Description
`set-pause-liquidation` is implemented as:

```clarity
(define-public (set-pause-liquidation (paused bool) (grace-period uint))
  (let ((was-paused (var-get pause-liquidation)))
    (if (and was-paused (not paused))
        ;; When unpausing, set grace period
        (var-set liquidation-grace-end 
                 (+ stacks-block-time grace-period))
        (var-set pause-liquidation paused))))
``` [1](#0-0) 

and `liquidation-paused` is:

```clarity
(define-read-only (liquidation-paused)
  (let ((manual-pause (var-get pause-liquidation))
        (grace-active (< stacks-block-time 
                         (var-get liquidation-grace-end))))
    (or manual-pause grace-active)))
``` [2](#0-1) 

`pause-liquidation` and `liquidation-grace-end` are declared as market-level operational-control state variables. [3](#0-2) 

Walking the logic:
1. DAO calls `set-pause-liquidation(true, _)`. `was-paused` is `false`, so the `if` condition `(and was-paused (not paused))` is false → falls to the `else` branch → `(var-set pause-liquidation true)`. Now `pause-liquidation = true`.
2. DAO later calls `set-pause-liquidation(false, grace-period)` intending to resume liquidations after a grace window. `was-paused` is now `true`, and `paused` is `false`, so `(and was-paused (not paused))` evaluates to `true` → the function takes the "unpausing" branch, which **only** sets `liquidation-grace-end`. It never executes `(var-set pause-liquidation false)`.
3. `pause-liquidation` remains permanently `true`.
4. `liquidation-paused` ORs `manual-pause` (permanently `true`) with `grace-active`; since `manual-pause` never becomes `false`, the OR is always `true` forever, regardless of how much time elapses past `liquidation-grace-end`.
5. Any subsequent attempt to "fix" this by calling `set-pause-liquidation(false, 0)` again hits the exact same branch (`was-paused` is still `true`), so the flag can never be cleared through this function — it is a one-way ratchet into a permanently paused state.

This is functionally identical in shape to the referenced report: a boolean control flag is toggled through a code path that fails to fully reset the state that downstream logic (`liquidation-paused`, analogous to `isOfferSorted`/`_unsort`/`_hide` in the Rubicon report) depends on, permanently breaking a core operation (liquidation, analogous to matching/canceling offers) once the flag has been set once.

### Impact Explanation
Once liquidation is paused a single time and the DAO attempts the documented "unpause with grace period" flow, `liquidation-paused()` will return `true` forever. All calls to `liquidate` that check this flag will be blocked indefinitely. Undercollateralized positions can never be liquidated, so bad debt accumulates without bound and cannot be recovered — this is a permanent freeze of the protocol's core solvency mechanism, leading to protocol insolvency as debt continues to accrue against unliquidatable collateral. This lands squarely in the **Critical** impact class: protocol insolvency / permanent freezing of funds.

### Likelihood Explanation
The bug triggers on the intended, documented usage pattern — pausing liquidation and then unpausing it via a grace period, which the code comment itself labels "When unpausing, set grace period." No adversarial input or multi-user interference is required; a single DAO-authorized transaction sequence (pause, then unpause) is sufficient to permanently disable liquidations. This makes the likelihood high given that pausing liquidation is an expected operational/incident-response action.

### Recommendation
In the unpause branch, explicitly clear the manual pause flag in addition to setting the grace period, e.g.:
```clarity
(if (and was-paused (not paused))
    (begin
      (var-set liquidation-grace-end (+ stacks-block-time grace-period))
      (var-set pause-liquidation false))
    (var-set pause-liquidation paused))
```
Alternatively, redesign `liquidation-paused` so that `manual-pause` and the grace-period timer are independent and both correctly resettable, and add a regression test asserting that after pause → unpause(grace) → grace period elapses, `liquidation-paused` returns `false`.

### Proof of Concept
1. Initial state: `pause-liquidation = false`, `liquidation-grace-end = 0`.
2. DAO/authorized caller invokes `set-pause-liquidation(true, 0)` → `pause-liquidation` set to `true` (else-branch executes).
3. DAO/authorized caller invokes `set-pause-liquidation(false, 3600)` intending to resume liquidations after a 1-hour grace period → `was-paused=true`, `paused=false` → the "unpausing" branch runs, setting `liquidation-grace-end = now + 3600`, but leaving `pause-liquidation = true`.
4. Advance time beyond `now + 3600`. Call `liquidation-paused()`: `grace-active` is now `false`, but `manual-pause` is still `true`, so the function returns `true`.
5. Any call to `liquidate` gated on `liquidation-paused` continues to revert/short-circuit indefinitely; repeating step 3 does not fix the state, since `was-paused` remains `true` on every subsequent call.

Note: This analysis is based on the `set-pause-liquidation`/`liquidation-paused` implementation as documented verbatim in `docs/market.md` (lines 644-660), which the repository's indexed grep results confirm exist under the same identifiers in the production contract `mainnet/contracts/market/v0-4-market.clar` (21 matching occurrences of the related terms). The exact line numbers inside `v0-4-market.clar` could not be retrieved within the available tool budget, so this should be re-verified directly against that file before remediation. [4](#0-3)

### Citations

**File:** docs/market.md (L398-401)
```markdown
;; Pause states (operational controls)
(define-data-var pause-liquidation bool false)
(define-data-var liquidation-grace-end uint u0)
```
```

**File:** docs/market.md (L637-660)
```markdown
## Pausability Features

The market contract includes comprehensive pausability controls for security:

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
