def test_a_scattered_learned_sample_is_kept_and_still_learns():
    """A start that disagrees must not lock learning out of its third sample."""
    from sendspin_bridge.config.players import record_air_learned_sample

    key = ("sbc", 48000, "00:02:72:0A:E4:3B")
    entries = [
        {
            "codec": "sbc",
            "rate": 48000,
            "adapter": key[2],
            "source": "learned",
            "samples_ms": [243.8],
            "d_out_ms": [0.0],
            "latency_ms": 243.8,
            "d_ref_ms": 0.0,
            "starts": 1,
            "measured_at": "2026-10-30T12:52:39+00:00",
        }
    ]

    out = record_air_learned_sample(entries, key, 120.0, 0.0, "2026-10-30T13:00:00+00:00")

    entry = next(e for e in out if (e["codec"], e["rate"], e["adapter"]) == key)
    assert entry["samples_ms"] == [243.8, 120.0]
    assert entry["starts"] == 2
