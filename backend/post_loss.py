"""Post-loss behavior helpers. Shared by backtester and bot_runner."""


def is_post_loss_trigger(exit_reason: str, trigger: str) -> bool:
    """Return True if this exit_reason should count toward post-loss counters
    (skip-after-stop and dynamic sizing) under the given trigger setting.

    trigger: "sl" → only hard stop-loss exits
             "tsl" → only trailing-stop exits
             "both" → either
    Unknown trigger values fall back to "sl"."""
    if trigger == "both":
        return exit_reason in ("stop_loss", "trailing_stop")
    if trigger == "tsl":
        return exit_reason == "trailing_stop"
    return exit_reason == "stop_loss"
