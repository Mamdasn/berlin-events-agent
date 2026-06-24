import psycopg2.extras

from agent.config import config
from agent.db.client import Database, db

_BASE_SELECT = """
    SELECT e.id, e.datum, e.von, e.bis, e.thema, e.category,
           e.description, e.source, l.name AS location, l.district,
           l.lat, l.lon
    FROM events e
    JOIN locations l ON l.id = e.location_id
"""

_DISTANCE_KM = """
    6371 * 2 * asin(sqrt(
        power(sin(radians(l.lat - %s) / 2), 2)
        + cos(radians(%s)) * cos(radians(l.lat))
        * power(sin(radians(l.lon - %s) / 2), 2)
    ))
"""


class EventRepository:
    def __init__(self, database: Database = db, cfg=config):
        self.db = database
        self.config = cfg

    def _event(self, row):
        return {
            "id": row["id"],
            "title": row["thema"],
            "date": row["datum"].isoformat() if row["datum"] else None,
            "time": str(row["von"]) if row["von"] else None,
            "end": str(row["bis"]) if row["bis"] else None,
            "category": row["category"],
            "description": (row["description"] or "")[: self.config.AGENT_FIELD_MAX_CHARS],
            "location": row["location"],
            "district": row["district"],
            "lat": float(row["lat"]) if row["lat"] is not None else None,
            "lon": float(row["lon"]) if row["lon"] is not None else None,
        }

    def _cap(self, limit):
        return min(int(limit or self.config.AGENT_RESULT_LIMIT), self.config.AGENT_RESULT_LIMIT)

    def search(self, date_from=None, date_to=None, district=None, category=None,
               keyword=None, limit=None):
        conditions = ["l.lat IS NOT NULL"]
        params = []
        if date_from:
            conditions.append("e.datum >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("e.datum <= %s")
            params.append(date_to)
        if district:
            conditions.append("l.district ILIKE %s")
            params.append(district)
        if category:
            conditions.append("e.category ILIKE %s")
            params.append(f"%{category}%")
        if keyword:
            conditions.append("(e.thema ILIKE %s OR e.description ILIKE %s)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])

        sql = (
            _BASE_SELECT
            + " WHERE " + " AND ".join(conditions)
            + " ORDER BY e.datum ASC, e.von ASC, e.id ASC LIMIT %s"
        )
        params.append(self._cap(limit))
        return [self._event(r) for r in self.db.query(sql, params)]

    def by_ids(self, event_ids):
        ids = [int(i) for i in event_ids]
        if not ids:
            return []
        sql = _BASE_SELECT + " WHERE e.id = ANY(%s) ORDER BY e.datum ASC, e.von ASC"
        return [self._event(r) for r in self.db.query(sql, (ids,))]

    def nearby(self, lat, lon, radius_km=None, date_from=None, date_to=None, limit=None):
        radius_km = float(radius_km if radius_km is not None else self.config.NEARBY_RADIUS_KM)
        lat, lon = float(lat), float(lon)
        conditions = ["l.lat IS NOT NULL"]
        params = [lat, lat, lon]
        if date_from:
            conditions.append("e.datum >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("e.datum <= %s")
            params.append(date_to)

        sql = (
            _BASE_SELECT.rstrip()
            + ",\n           " + _DISTANCE_KM + " AS distance_km\n"
            + " WHERE " + " AND ".join(conditions)
            + "\n    AND " + _DISTANCE_KM + " <= %s"
            + " ORDER BY distance_km ASC, e.datum ASC LIMIT %s"
        )
        params.extend([lat, lat, lon, radius_km, self._cap(limit)])

        out = []
        for r in self.db.query(sql, params):
            event = self._event(r)
            event["distance_km"] = round(float(r["distance_km"]), 3)
            out.append(event)
        return out

    def on_date(self, date, limit=2000):
        sql = (
            _BASE_SELECT
            + " WHERE e.datum = %s AND l.lat IS NOT NULL"
            + " ORDER BY e.von ASC, e.id ASC LIMIT %s"
        )
        return [self._event(r) for r in self.db.query(sql, (date, int(limit)))]

    def count(self):
        rows = self.db.query(
            "SELECT count(*) AS n FROM events e "
            "JOIN locations l ON l.id = e.location_id WHERE l.lat IS NOT NULL"
        )
        return int(rows[0]["n"])

    def unique_events(self, limit=None):
        sql = """
            SELECT DISTINCT ON (lower(btrim(e.thema)))
                   e.id, e.thema, e.category, e.description
            FROM events e
            JOIN locations l ON l.id = e.location_id
            WHERE l.lat IS NOT NULL AND e.thema IS NOT NULL
            ORDER BY lower(btrim(e.thema)), e.id ASC
        """
        if limit:
            sql += " LIMIT %s"
        rows = self.db.query(sql, (int(limit),) if limit else None)
        return [
            {
                "id": int(r["id"]),
                "title": r["thema"],
                "category": r["category"],
                "description": r["description"],
            }
            for r in rows
        ]

    def embedding_corpus(self):
        for row in self.unique_events():
            parts = [row["title"], row["category"], row["description"]]
            yield row["id"], " — ".join(p for p in parts if p)

    def feature(self, event_ids, note=None, selected_by=None):
        ids = [int(i) for i in event_ids]
        if not ids:
            return 0
        rows = [(i, note, selected_by) for i in ids]

        def run(cur):
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO editors_choice (event_id, note, selected_by)
                VALUES %s
                ON CONFLICT (event_id)
                DO UPDATE SET note = EXCLUDED.note,
                              selected_by = EXCLUDED.selected_by,
                              selected_at = now()
                """,
                rows,
            )
            return cur.rowcount

        return self.db.execute(run)

    def featured(self, limit=200):
        sql = """
            SELECT ec.event_id, e.thema, ec.note, ec.selected_by, ec.selected_at,
                   e.datum, e.von, l.name AS location
            FROM editors_choice ec
            JOIN events e ON e.id = ec.event_id
            JOIN locations l ON l.id = e.location_id
            ORDER BY ec.selected_at DESC
            LIMIT %s
        """
        return [
            {
                "event_id": r["event_id"],
                "title": r["thema"],
                "note": r["note"],
                "selected_by": r["selected_by"],
                "selected_at": r["selected_at"].isoformat() if r["selected_at"] else None,
                "date": r["datum"].isoformat() if r["datum"] else None,
                "time": str(r["von"]) if r["von"] else None,
                "location": r["location"],
            }
            for r in self.db.query(sql, (int(limit),))
        ]

    def unfeature(self, event_id):
        def run(cur):
            cur.execute(
                "DELETE FROM editors_choice WHERE event_id = %s", (int(event_id),)
            )
            return cur.rowcount

        return self.db.execute(run) > 0


events = EventRepository()
