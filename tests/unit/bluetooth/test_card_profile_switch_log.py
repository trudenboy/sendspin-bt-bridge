"""The card-profile switch says which profiles the speaker's card has (#476).

An Echo's card comes up as ``a2dp_source`` with only source-side profiles;
the warning has to show that, or a report cannot tell it from a speaker
whose sink profile just failed to start.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from sendspin_bridge.bluetooth import audio


def test_switch_warning_lists_the_cards_profiles(caplog):
    cards = [
        {
            "name": "bluez_card.AA_BB_CC_DD_EE_FF",
            "active_profile": "a2dp_source",
            "profiles": ["a2dp_source", "a2dp_sink", "off"],
        }
    ]
    caplog.set_level(logging.WARNING, logger=audio.logger.name)
    with (
        patch.object(audio, "list_cards", return_value=cards),
        patch.object(audio, "set_card_profile", return_value=False),
    ):
        assert audio._switch_card_profile_to_a2dp("AA_BB_CC_DD_EE_FF") is False

    line = next(r.getMessage() for r in caplog.records if "is in profile" in r.getMessage())
    assert "a2dp_source, a2dp_sink, off" in line
