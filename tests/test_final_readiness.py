from pathlib import Path

import core.service as service


def test_save_upload_strips_path_components(tmp_path, monkeypatch):
    upload_dir = tmp_path / 'uploads'
    upload_dir.mkdir()
    monkeypatch.setattr(service, 'UPLOAD_DIR', upload_dir)
    saved = service.save_upload('../../evil.pdf', b'safe')
    assert saved == upload_dir / 'evil.pdf'
    assert saved.read_bytes() == b'safe'
    assert saved.resolve().is_relative_to(upload_dir.resolve())
