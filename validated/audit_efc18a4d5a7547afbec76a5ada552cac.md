**No vulnerability found for this question.**

The premise of the exploit — that `limiter.current_amount + new_tokens` could "wrap" and silently corrupt state — does not hold on the Aptos Move VM. Move performs checked arithmetic on all integer operations by default: an addition that would overflow a `u64` triggers an `ARITHMETIC_ERROR` abort rather than wrapping around [1](#0-0) .

Concretely, in `refill`:
- `time_passed * limiter.capacity` (line 47) and `limiter.current_amount + new_tokens` (line 49) are both plain `u64` additions/multiplications with no explicit overflow handling.
- If an attacker engineers a very large `time_passed` (e.g., via a long-idle account) such that either of these operations would overflow `u64::MAX`, the Move VM aborts the transaction at that instruction instead of computing a wrapped value.
- Because the abort happens before any write set is produced, no state change from this transaction is committed at all — there is no scenario where a "wrong" `current_amount` gets written to storage.

So the described data-corruption path (a comparison taking the "wrong branch" due to wraparound and persisting an incorrect `current_amount`) is architecturally impossible in Move; the worst outcome is transaction failure (an availability/DoS concern), which is explicitly out of scope per the review rules (generic DoS excluded) and does not corrupt committed state, proof material, or authenticated responses. [1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/account/rate_limiter.move (L43-58)
```text
    fun refill(limiter: &mut RateLimiter) {
        let current_time = timestamp::now_seconds();
        let time_passed = current_time - limiter.last_refill_timestamp;
        // Calculate the full tokens that can be added
        let accumulated_amount = time_passed * limiter.capacity + limiter.fractional_accumulated;
        let new_tokens = accumulated_amount / limiter.refill_interval;
        if (limiter.current_amount + new_tokens >= limiter.capacity) {
            limiter.current_amount = limiter.capacity;
            limiter.fractional_accumulated = 0;
        } else {
            limiter.current_amount += new_tokens;
            // Update the fractional amount accumulated for the next refill cycle
            limiter.fractional_accumulated = accumulated_amount % limiter.refill_interval;
        };
        limiter.last_refill_timestamp = current_time;
    }
```
