from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

from post_loss import is_post_loss_trigger


def test_sl_only_counts_hard_stop():
    assert is_post_loss_trigger("stop_loss", "sl") is True
    assert is_post_loss_trigger("trailing_stop", "sl") is False
    assert is_post_loss_trigger("signal", "sl") is False


def test_tsl_only_counts_trailing():
    assert is_post_loss_trigger("stop_loss", "tsl") is False
    assert is_post_loss_trigger("trailing_stop", "tsl") is True
    assert is_post_loss_trigger("signal", "tsl") is False


def test_both_counts_either_stop():
    assert is_post_loss_trigger("stop_loss", "both") is True
    assert is_post_loss_trigger("trailing_stop", "both") is True
    assert is_post_loss_trigger("signal", "both") is False


def test_unknown_trigger_defaults_to_sl():
    assert is_post_loss_trigger("stop_loss", "garbage") is True
    assert is_post_loss_trigger("trailing_stop", "garbage") is False
