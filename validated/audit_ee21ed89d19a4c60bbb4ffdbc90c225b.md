I'll analyze the external bug's core invariant and search for a nearcore analog. The bug is about a "warn then act" pattern where a no-op/partial action clears a protective warning state, allowing indefinite delay of a critical process.

Let me search for similar patterns in nearcore.