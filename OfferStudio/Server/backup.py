import os,sqlite3,time,contextlib
from pathlib import Path
import core
core.init()
folder=core.DATA/'backups';folder.mkdir(mode=0o700,exist_ok=True)
path=folder/time.strftime('offer-studio-%Y-%m-%d.db',time.gmtime())
with core.db() as source, contextlib.closing(sqlite3.connect(path)) as target:source.backup(target)
path.chmod(0o600)
for old in folder.glob('offer-studio-*.db'):
    if old.stat().st_mtime<time.time()-30*86400:old.unlink()
with core.db() as c:
    c.execute('DELETE FROM events WHERE ts<?',(core.now()-90*86400,))
