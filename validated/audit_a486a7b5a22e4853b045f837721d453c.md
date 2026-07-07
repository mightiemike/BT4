Looking at the Nado codebase, I need to find a pattern where:
1. State is keyed by a "slot" or "position ID" (not by address)
2. The holder of that slot can be changed
3. The old holder's state persists for the new holder, allowing bypass of a check

Let me check the builder fee system and clearinghouse for any remaining patterns.