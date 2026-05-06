from modules.module_model_downloader import download_model


def test_download_model_creates_parent_and_downloads_when_missing(tmp_path, monkeypatch):
    target = tmp_path / "models" / "face.task"
    calls = []

    def fake_urlretrieve(url, path):
        calls.append((url, path))
        target.write_bytes(b"model")

    monkeypatch.setattr("modules.module_model_downloader.urllib.request.urlretrieve", fake_urlretrieve)

    download_model("https://example.test/model", str(target))

    assert calls == [("https://example.test/model", str(target))]
    assert target.read_bytes() == b"model"


def test_download_model_skips_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "models" / "face.task"
    target.parent.mkdir()
    target.write_bytes(b"existing")
    calls = []
    monkeypatch.setattr(
        "modules.module_model_downloader.urllib.request.urlretrieve",
        lambda url, path: calls.append((url, path)),
    )

    download_model("https://example.test/model", str(target))

    assert calls == []
    assert target.read_bytes() == b"existing"
