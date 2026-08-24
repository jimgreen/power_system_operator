from __future__ import annotations

import subprocess
import sys

from power_operator.database import Database, initialize_database
from power_operator.models import OperatorLog


WRITER_CODE = """
import sys
from power_operator.database import Database
from power_operator.models import OperatorLog
database = Database(sys.argv[1])
base = int(sys.argv[2])
for offset in range(30):
    value = base + offset
    database.write(lambda session, value=value: session.add(OperatorLog(
        log_time=value, simu_time=value, log_type=1, log_info=f'process-{value}'
    )))
"""


def test_independent_processes_can_write_same_database(tmp_path):
    path = tmp_path / "ems.db"
    database = Database(path)
    initialize_database(database)
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", WRITER_CODE, str(path), str(index * 1000)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(4)
    ]
    failures = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        if process.returncode != 0:
            failures.append((process.returncode, stdout, stderr))
    assert not failures
    with database.session() as session:
        assert session.query(OperatorLog).count() == 120
