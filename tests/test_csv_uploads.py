import io
from pathlib import Path
import pytest
from sqlalchemy import select, func
from streamlit.testing.v1 import AppTest
from pipeline.db import sessions
from pipeline.intake import IntakeService
from pipeline.models import ClientTaskRevision
from pipeline.csv_uploads import TaskUploadBatch, UploadedTaskCSV, save_csv_batch

class File(io.BytesIO):
    def __init__(self, name, data):
        super().__init__(data)
        self.name = name

def setup(factory):
    with factory.begin() as s:
        tid = IntakeService(s).save(dict(client_name='Lab', task_name='CSV tasks', required_capabilities='CSV processing',
            outside_scope='No network', quantities=[dict(category='CSV', minimum_tasks=1, attempts_per_task=1)]))
        return s.scalar(select(ClientTaskRevision.id).where(ClientTaskRevision.task_id == tid))

def test_thousands_of_arbitrary_csvs(tmp_path):
    factory = sessions(f'sqlite:///{tmp_path / "bulk.db"}')
    rid = setup(factory)
    uploads = [File(f'task-{i}.csv', f'any,column\n{i},value\n'.encode()) for i in range(2000)]
    uploads.append(File('../../outside.csv', b'completely,different,columns\na,b,c\n'))
    root = tmp_path / 'files'
    with factory.begin() as s:
        bid = save_csv_batch(s, rid, uploads, 'request-1', root)
        assert save_csv_batch(s, rid, uploads, 'request-1', root) == bid
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(UploadedTaskCSV)) == 2001
        assert s.scalar(select(func.count()).select_from(TaskUploadBatch)) == 1
        rows = list(s.scalars(select(UploadedTaskCSV)))
        assert len({r.id for r in rows}) == 2001
        for r in rows:
            # SQLite does not enforce VARCHAR lengths; PostgreSQL does.
            assert len(r.id) <= UploadedTaskCSV.__table__.c.id.type.length
            assert Path(r.path).resolve().is_relative_to(root.resolve())
        assert rows[0].row_count == 2
        assert Path(rows[0].path).read_bytes() == uploads[0].getvalue()

@pytest.mark.parametrize('upload', [File('empty.csv', b''), File('bad.csv', b'"unterminated'), File('data.json', b'[]'), File('bad.csv', b'\xff')])
def test_bad_batch_rolls_back_without_leaving_files(tmp_path, upload):
    factory = sessions(f'sqlite:///{tmp_path / "bad.db"}')
    rid = setup(factory)
    root = tmp_path / 'files'
    with pytest.raises(ValueError):
        with factory.begin() as s:
            save_csv_batch(s, rid, [File('okay.csv', b'a,b\n1,2'), upload], 'bad', root)
    assert not list(root.iterdir())
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(TaskUploadBatch)) == 0

def test_bulk_upload_ui(tmp_path, monkeypatch):
    url = f'sqlite:///{tmp_path / "ui.db"}'
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setattr('pipeline.csv_uploads.ROOT', tmp_path / 'files')
    setup(sessions(url))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=30).run()
    app.switch_page('pages/2_Upload_submission.py').run()
    assert not app.exception
    assert next(b for b in app.button if b.label == 'Upload task files').disabled
    app.file_uploader[0].upload('task-1.csv', b'my,columns\n1,2', 'text/csv')
    app.file_uploader[0].upload('task-2.csv', b'other\n3', 'text/csv')
    app.run()
    next(b for b in app.button if b.label == 'Upload task files').click().run()
    assert not app.exception
    assert app.success
    assert '2 task files' in app.success[0].value
    next(b for b in app.button if b.label == 'Upload task files').click().run()
    with sessions(url)() as s:
        assert s.scalar(select(func.count()).select_from(UploadedTaskCSV)) == 2
