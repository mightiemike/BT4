Looking at the repository, I need to find an analog to the batchId collision vulnerability class: **two execution paths share the same state/resource, one path enforces a critical invariant, the other does not, and the "secure" path internally calls the "insecure" path — but the "insecure" path remains independently reachable**.

Let me read the key verification functions more carefully.