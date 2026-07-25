I'll analyze the external bug (front-running a "rebalancing" event to capture a sudden price-per-share surge) and search for a nearcore analog. The key invariant: a pool's share price should not have sudden jumps exploitable by deposit-then-withdraw in the same block/epoch boundary.

Let me search for relevant nearcore code: