Looking at the external report's vulnerability class — **duplicate claim via repeated use of the same identifier in a single call, bypassing a one-time-per-interaction accounting invariant** — I need to find an analog in this Cosmos POS chain repository.

Let me trace the `ClaimTierRewards` message handler and its downstream accounting.