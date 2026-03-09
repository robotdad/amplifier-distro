from distro_plugin.config import DistroPluginSettings


def test_default_paths():
    s = DistroPluginSettings()
    assert str(s.distro_home).endswith(".amplifier-distro")
    assert str(s.amplifier_home).endswith(".amplifier")


def test_custom_paths(tmp_path):
    s = DistroPluginSettings(distro_home=tmp_path / "d", amplifier_home=tmp_path / "a")
    assert s.distro_home == tmp_path / "d"
    assert s.amplifier_home == tmp_path / "a"


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DISTRO_PLUGIN_DISTRO_HOME", str(tmp_path / "custom"))
    s = DistroPluginSettings()
    assert s.distro_home == tmp_path / "custom"
