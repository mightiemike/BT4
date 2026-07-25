Looking at the external report's bug class: a "max" reporting function uses one computation path, but the actual execution path uses a different (stricter) one, allowing the reported maximum to be exceeded in practice. I need to find the nearcore analog.

Let me trace the receipt size validation vs. execution mutation path.