import aiosqlite
from datetime import date, datetime


class Database:
    def __init__(self, path: str):
        self.path = path

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS players (
                    tg_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    nickname TEXT,
                    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS trainings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    time TEXT NOT NULL DEFAULT '20:00',
                    location TEXT DEFAULT 'Обычное место',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS attendance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    training_id INTEGER NOT NULL,
                    player_id INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('yes','no','maybe')),
                    voted_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(training_id) REFERENCES trainings(id),
                    FOREIGN KEY(player_id) REFERENCES players(tg_id),
                    UNIQUE(training_id, player_id)
                );

                CREATE TABLE IF NOT EXISTS polls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT NOT NULL,
                    options TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    msg_id INTEGER,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS poll_votes (
                    poll_id INTEGER NOT NULL,
                    player_id INTEGER NOT NULL,
                    option_index INTEGER NOT NULL,
                    voted_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(poll_id) REFERENCES polls(id),
                    FOREIGN KEY(player_id) REFERENCES players(tg_id),
                    UNIQUE(poll_id, player_id)
                );

                CREATE TABLE IF NOT EXISTS matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    opponent TEXT NOT NULL,
                    our_score INTEGER,
                    their_score INTEGER,
                    location TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS match_players (
                    match_id INTEGER NOT NULL,
                    player_id INTEGER NOT NULL,
                    goals INTEGER DEFAULT 0,
                    assists INTEGER DEFAULT 0,
                    FOREIGN KEY(match_id) REFERENCES matches(id),
                    FOREIGN KEY(player_id) REFERENCES players(tg_id),
                    UNIQUE(match_id, player_id)
                );
            """)
            await db.commit()

    # -- Players --
    async def register_player(self, tg_id: int, name: str, nickname: str = None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO players (tg_id, name, nickname) VALUES (?, ?, ?)",
                (tg_id, name, nickname),
            )
            await db.commit()

    async def get_all_players(self):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM players WHERE active = 1 ORDER BY name"
            )
            return await cur.fetchall()

    async def get_player(self, tg_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM players WHERE tg_id = ?", (tg_id,))
            return await cur.fetchone()

    async def deactivate_player(self, tg_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE players SET active = 0 WHERE tg_id = ?", (tg_id,)
            )
            await db.commit()

    # -- Trainings --
    async def create_training(self, date_str: str, time_str: str = "20:00", location: str = "Обычное место"):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO trainings (date, time, location) VALUES (?, ?, ?)",
                (date_str, time_str, location),
            )
            await db.commit()
            return cur.lastrowid

    async def get_training(self, training_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM trainings WHERE id = ?", (training_id,))
            return await cur.fetchone()

    async def get_upcoming_trainings(self, limit: int = 5):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM trainings WHERE date >= date('now') ORDER BY date LIMIT ?",
                (limit,),
            )
            return await cur.fetchall()

    async def get_last_training(self):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM trainings WHERE date <= date('now') ORDER BY date DESC LIMIT 1"
            )
            return await cur.fetchone()

    # -- Attendance --
    async def set_attendance(self, training_id: int, player_id: int, status: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO attendance (training_id, player_id, status, voted_at)
                   VALUES (?, ?, ?, datetime('now'))""",
                (training_id, player_id, status),
            )
            await db.commit()

    async def get_training_attendance(self, training_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT a.*, p.name, p.nickname
                   FROM attendance a
                   JOIN players p ON a.player_id = p.tg_id
                   WHERE a.training_id = ?
                   ORDER BY a.status""",
                (training_id,),
            )
            return await cur.fetchall()

    async def get_player_stats(self, tg_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'yes' THEN 1 ELSE 0 END) as yes,
                    SUM(CASE WHEN status = 'no' THEN 1 ELSE 0 END) as no,
                    SUM(CASE WHEN status = 'maybe' THEN 1 ELSE 0 END) as maybe
                   FROM attendance WHERE player_id = ?""",
                (tg_id,),
            )
            return await cur.fetchone()

    async def get_top_attendance(self, limit: int = 10):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT p.name, p.nickname,
                    COUNT(a.id) as total,
                    SUM(CASE WHEN a.status = 'yes' THEN 1 ELSE 0 END) as yes
                   FROM players p
                   LEFT JOIN attendance a ON p.tg_id = a.player_id
                   WHERE p.active = 1
                   GROUP BY p.tg_id
                   ORDER BY yes DESC
                   LIMIT ?""",
                (limit,),
            )
            return await cur.fetchall()

    # -- Polls --
    async def create_poll(self, question: str, options: list[str], chat_id: int):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO polls (question, options, chat_id) VALUES (?, ?, ?)",
                (question, "|||".join(options), chat_id),
            )
            await db.commit()
            return cur.lastrowid

    async def set_poll_msg_id(self, poll_id: int, msg_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE polls SET msg_id = ? WHERE id = ?", (msg_id, poll_id))
            await db.commit()

    async def get_poll(self, poll_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM polls WHERE id = ?", (poll_id,))
            return await cur.fetchone()

    async def vote_poll(self, poll_id: int, player_id: int, option_index: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO poll_votes (poll_id, player_id, option_index, voted_at)
                   VALUES (?, ?, ?, datetime('now'))""",
                (poll_id, player_id, option_index),
            )
            await db.commit()

    async def get_poll_results(self, poll_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT option_index, COUNT(*) as count
                   FROM poll_votes WHERE poll_id = ?
                   GROUP BY option_index""",
                (poll_id,),
            )
            rows = await cur.fetchall()
            results = {}
            for r in rows:
                results[r["option_index"]] = r["count"]
            return results

    # -- Matches --
    async def create_match(self, date_str: str, opponent: str, location: str = ""):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO matches (date, opponent, location) VALUES (?, ?, ?)",
                (date_str, opponent, location),
            )
            await db.commit()
            return cur.lastrowid

    async def set_match_score(self, match_id: int, our: int, their: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE matches SET our_score = ?, their_score = ? WHERE id = ?",
                (our, their, match_id),
            )
            await db.commit()

    async def get_match(self, match_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
            return await cur.fetchone()

    async def get_matches(self, limit: int = 10):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM matches ORDER BY date DESC LIMIT ?", (limit,)
            )
            return await cur.fetchall()

    async def add_match_player(self, match_id: int, player_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO match_players (match_id, player_id) VALUES (?, ?)",
                (match_id, player_id),
            )
            await db.commit()