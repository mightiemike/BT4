[1](#0-0)

### Citations

**File:** stackslib/src/chainstate/stacks/boot/signers.clar (L28-33)
```text
(define-private (set-signers
                 (reward-cycle uint)
                 (signers (list 4000 { signer: principal, weight: uint })))
     (begin
      (asserts! (is-eq (var-get last-set-cycle) reward-cycle) (err ERR_CYCLE_NOT_SET))
      (ok (map-set cycle-signer-set reward-cycle signers))))
```
