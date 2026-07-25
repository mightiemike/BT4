I'll systematically search for the analog in nearcore. The bug class is: **uninitialized "before" checkpoint in a delta calculation at an exact boundary condition**, causing a `current - 0` inflation that lets an attacker claim historical accumulated values they didn't earn.

Let me map this to nearcore's reward/balance tracking systems.