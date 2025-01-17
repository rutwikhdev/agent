import os
from agent.server import Server
from pathlib import Path
from datetime import datetime
import re
from peewee import MySQLDatabase


class DatabaseServer(Server):
    def __init__(self, directory=None):
        self.directory = directory or os.getcwd()
        self.config_file = os.path.join(self.directory, "config.json")
        self.name = self.config["name"]

        self.mariadb_directory = "/var/lib/mysql"

    def search_binary_log(
        self,
        log,
        database,
        start_datetime,
        stop_datetime,
        search_pattern,
        max_lines,
    ):
        log = os.path.join(self.mariadb_directory, log)
        LINES_TO_SKIP = r"^(USE|COMMIT|START TRANSACTION|DELIMITER|ROLLBACK|#)"
        command = (
            f"mysqlbinlog --short-form --database {database} "
            f"--start-datetime '{start_datetime}' "
            f"--stop-datetime '{stop_datetime}' "
            f" {log} | grep -Piv '{LINES_TO_SKIP}' | head -n {max_lines}"
        )

        DELIMITER = "/*!*/;"

        events = []
        timestamp = 0
        for line in self.execute(command, skip_output_log=True)[
            "output"
        ].split(DELIMITER):
            line = line.strip()
            if line.startswith("SET TIMESTAMP"):
                timestamp = int(line.split("=")[-1].split(".")[0])
            else:
                if any(line.startswith(skip) for skip in ["SET", "/*!"]):
                    continue
                elif line and timestamp and re.search(search_pattern, line):
                    events.append(
                        {
                            "query": line,
                            "timestamp": str(
                                datetime.utcfromtimestamp(timestamp)
                            ),
                        }
                    )
        return events

    @property
    def binary_logs(self):
        BINARY_LOG_FILE_PATTERN = r"mysql-bin.\d+"
        files = []
        for file in Path(self.mariadb_directory).iterdir():
            if re.match(BINARY_LOG_FILE_PATTERN, file.name):
                unix_timestamp = int(file.stat().st_mtime)
                files.append(
                    {
                        "name": file.name,
                        "size": file.stat().st_size,
                        "modified": str(
                            datetime.utcfromtimestamp(unix_timestamp)
                        ),
                    }
                )
        return sorted(files, key=lambda x: x["name"])

    def processes(self, private_ip, mariadb_root_password):
        processes = []
        try:
            mariadb = MySQLDatabase(
                "mysql",
                user="root",
                password=mariadb_root_password,
                host=private_ip,
                port=3306,
            )
            cursor = mariadb.execute_sql("SHOW FULL PROCESSLIST")
            rows = cursor.fetchall()
            columns = [d[0] for d in cursor.description]
            processes = list(map(lambda x: dict(zip(columns, x)), rows))
        except Exception:
            import traceback

            traceback.print_exc()
        return processes

    def kill_processes(
        self, private_ip, mariadb_root_password, kill_threshold
    ):
        processes = self.processes(private_ip, mariadb_root_password)
        try:
            mariadb = MySQLDatabase(
                "mysql",
                user="root",
                password=mariadb_root_password,
                host=private_ip,
                port=3306,
            )
            for process in processes:
                if (process["Time"] or 0) >= kill_threshold:
                    mariadb.execute_sql(f"KILL {process['Id']}")
        except Exception:
            import traceback

            traceback.print_exc()

    def get_deadlocks(
        self,
        database,
        start_datetime,
        stop_datetime,
        max_lines,
        private_ip,
        mariadb_root_password,
    ):
        mariadb = MySQLDatabase(
            "percona",
            user="root",
            password=mariadb_root_password,
            host=private_ip,
            port=3306,
        )

        cursor = mariadb.execute_sql(
            f"""
            select *
            from deadlock
            where user = %s
            and ts >= %s
            and ts <= %s
            order by ts
            limit {int(max_lines)}""",
            (database, start_datetime, stop_datetime),
        )
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return list(map(lambda x: dict(zip(columns, x)), rows))
