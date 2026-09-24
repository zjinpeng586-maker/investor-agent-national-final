from pathlib import Path

import core.service as service
from core.storage import workspace_context


def test_save_upload_strips_path_components(tmp_path, monkeypatch):
    upload_dir = tmp_path / 'uploads'
    upload_dir.mkdir()
    monkeypatch.setattr(service, 'UPLOAD_DIR', upload_dir)
    with workspace_context('main', tmp_path, allow_writes=True):
        saved = service.save_upload('../../evil.pdf', b'safe')
    assert saved.name == 'evil.pdf'
    assert saved.parent.parent == upload_dir
    assert saved.read_bytes() == b'safe'
    assert saved.resolve().is_relative_to(upload_dir.resolve())
