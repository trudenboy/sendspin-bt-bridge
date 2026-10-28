import pytest

from sendspin_bridge.services.audio.sink_suspend_policy import (
    SuspendObserver,
    SuspendPolicy,
    parse_pa_modules,
    parse_pactl_info,
    parse_pw_dump,
    probe_suspend_policy,
)


def test_pactl_info_tells_pipewire_from_pulseaudio():
    assert parse_pactl_info("Server Name: PulseAudio (on PipeWire 1.0.5)\n") == "pipewire"
    assert parse_pactl_info("Server Name: pulseaudio\nServer Version: 17.0\n") == "pulseaudio"
    assert parse_pactl_info("garbage") == "unknown"


def test_module_suspend_on_idle_with_timeout():
    p = parse_pa_modules("7\tmodule-suspend-on-idle\ttimeout=3\t\n8\tmodule-bluez5-discover\t\t\n")
    assert p == SuspendPolicy(server="pulseaudio", suspends=True, timeout_s=3.0, source="module-suspend-on-idle")


def test_module_suspend_on_idle_default_timeout_is_5s():
    assert parse_pa_modules("7\tmodule-suspend-on-idle\t\t\n").timeout_s == 5.0


def test_haos_has_no_suspend_module():
    p = parse_pa_modules("8\tmodule-bluez5-discover\t\t\n")
    assert p.suspends is False and p.source == "none"


def test_wireplumber_timeout_on_bluez_nodes():
    nodes = [
        {
            "type": "PipeWire:Interface:Node",
            "info": {
                "props": {"node.name": "bluez_output.FC_58.1", "session.suspend-timeout-seconds": 0},
            },
        }
    ]
    assert parse_pw_dump(nodes).suspends is False
    nodes[0]["info"]["props"]["session.suspend-timeout-seconds"] = 3
    p = parse_pw_dump(nodes)
    assert (p.suspends, p.timeout_s, p.source) == (True, 3.0, "wireplumber")


def test_absent_wireplumber_property_means_the_5s_default():
    nodes = [{"type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "bluez_output.FC_58.1"}}}]
    p = parse_pw_dump(nodes)
    assert (p.suspends, p.timeout_s, p.source) == (True, 5.0, "wireplumber")


def test_no_bluez_nodes_is_unknown():
    assert parse_pw_dump([]).suspends is None


@pytest.mark.asyncio
async def test_missing_pw_dump_falls_back_to_unknown():
    async def run(argv):
        if argv[0] == "pactl":
            return 0, "Server Name: PulseAudio (on PipeWire 1.0.5)\n"
        raise FileNotFoundError(argv[0])  # pw-dump not in the container

    p = await probe_suspend_policy(run=run)
    assert (p.server, p.suspends, p.source) == ("pipewire", None, "unknown")


@pytest.mark.asyncio
async def test_probe_never_raises():
    async def boom(argv):
        raise OSError("no pactl")

    p = await probe_suspend_policy(run=boom)
    assert p.server == "unknown" and p.suspends is None


@pytest.mark.asyncio
async def test_probe_reads_pulseaudio_modules():
    async def run(argv):
        if argv[:2] == ["pactl", "info"]:
            return 0, "Server Name: pulseaudio\n"
        return 0, "7\tmodule-suspend-on-idle\ttimeout=2\t\n"

    p = await probe_suspend_policy(run=run)
    assert (p.server, p.suspends, p.timeout_s) == ("pulseaudio", True, 2.0)


@pytest.mark.asyncio
async def test_probe_reads_pw_dump_json():
    async def run(argv):
        if argv[0] == "pactl":
            return 0, "Server Name: PulseAudio (on PipeWire 1.0.5)\n"
        return 0, '[{"info": {"props": {"node.name": "bluez_output.X.1", "session.suspend-timeout-seconds": 0}}}]'

    p = await probe_suspend_policy(run=run)
    assert (p.server, p.suspends, p.source) == ("pipewire", False, "wireplumber")


def test_observer_counts_only_host_suspends():
    t = [1000.0]
    obs = SuspendObserver(clock=lambda: t[0])
    obs.on_state("running", "idle", bridge_requested=False, connected=True)
    t[0] += 5.2
    obs.on_state("idle", "suspended", bridge_requested=False, connected=True)
    obs.on_state("running", "suspended", bridge_requested=True, connected=True)  # power_save: ours
    obs.on_state("running", "suspended", bridge_requested=False, connected=False)  # disconnecting
    snap = obs.snapshot(None)
    assert snap["observed"] == 1 and snap["last_idle_s"] == pytest.approx(5.2) and snap["warn"] is True
    assert snap["reason"] == "observed_suspend"


def test_nothing_observed_and_no_policy_gives_no_snapshot():
    assert SuspendObserver().snapshot(None) is None


def test_static_policy_below_60s_warns_without_observation():
    obs = SuspendObserver()
    snap = obs.snapshot(SuspendPolicy("pipewire", True, 5.0, "wireplumber"))
    assert snap["warn"] is True and snap["reason"] == "wireplumber_suspend_timeout"


def test_module_policy_reason():
    snap = SuspendObserver().snapshot(SuspendPolicy("pulseaudio", True, 5.0, "module-suspend-on-idle"))
    assert snap["warn"] is True and snap["reason"] == "module_suspend_on_idle"


def test_long_static_timeout_does_not_warn():
    snap = SuspendObserver().snapshot(SuspendPolicy("pipewire", True, 120.0, "wireplumber"))
    assert snap["warn"] is False and snap["reason"] is None


def test_warning_clears_after_24h_clean():
    t = [0.0]
    obs = SuspendObserver(clock=lambda: t[0])
    obs.on_state("idle", "suspended", bridge_requested=False, connected=True)
    t[0] += 86_401
    assert obs.snapshot(SuspendPolicy("pulseaudio", False, None, "none"))["warn"] is False


@pytest.mark.asyncio
async def test_probe_loop_hands_the_policy_to_every_client_and_repeats():
    import asyncio

    from sendspin_bridge.services.audio.sink_suspend_policy import run_suspend_probe_loop

    policy = SuspendPolicy("pipewire", True, 5.0, "wireplumber")
    calls = 0

    async def probe():
        nonlocal calls
        calls += 1
        return policy

    class FakeClient:
        def __init__(self):
            self.got = []

        def set_suspend_policy(self, p):
            self.got.append(p)

    clients = [FakeClient(), FakeClient()]
    task = asyncio.create_task(run_suspend_probe_loop(lambda: clients, interval_s=0.01, probe=probe))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls >= 2
    assert all(c.got and c.got[0] is policy for c in clients)


@pytest.mark.asyncio
async def test_probe_loop_survives_a_failing_client():
    import asyncio

    from sendspin_bridge.services.audio.sink_suspend_policy import run_suspend_probe_loop

    async def probe():
        return SuspendPolicy("pulseaudio", False, None, "none")

    class Bad:
        def set_suspend_policy(self, p):
            raise RuntimeError("boom")

    class Good:
        got = None

        def set_suspend_policy(self, p):
            self.got = p

    good = Good()
    task = asyncio.create_task(run_suspend_probe_loop(lambda: [Bad(), good], interval_s=0.01, probe=probe))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert good.got is not None


def test_a_sink_that_first_appears_suspended_is_not_a_host_suspend():
    obs = SuspendObserver()
    obs.on_state(None, "suspended", bridge_requested=False, connected=True)
    assert obs.snapshot(None) is None


@pytest.mark.asyncio
async def test_orchestrator_stops_the_probe_task():
    import asyncio

    from sendspin_bridge.bridge.orchestrator import BridgeOrchestrator

    o = BridgeOrchestrator()
    o._suspend_probe_task = asyncio.create_task(asyncio.sleep(60))
    task = o._suspend_probe_task
    await o._stop_suspend_probe()
    assert task.cancelled() and o._suspend_probe_task is None


@pytest.mark.asyncio
async def test_probe_loop_remembers_the_last_policy():
    import asyncio

    from sendspin_bridge.services.audio import sink_suspend_policy as m

    policy = SuspendPolicy("pulseaudio", True, 2.0, "module-suspend-on-idle")

    async def probe():
        return policy

    task = asyncio.create_task(m.run_suspend_probe_loop(lambda: [], interval_s=0.01, probe=probe))
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert m.last_suspend_policy() == policy
