#!/usr/bin/env python3
"""Dynatrace Classic Services inventory -> SQLite + CSV. Python 3.9+, requests."""
import csv
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = os.environ.get('DT_URL', '').rstrip('/')
TOKEN = os.environ.get('DT_API_TOKEN', '')
DAYS = int(os.environ.get('DT_LOOKBACK_DAYS', '30'))
MISSING_LIMIT = int(os.environ.get('DT_MISSING_RUNS', '3'))
MIN_RESULT_RATIO = float(os.environ.get('DT_MIN_RESULT_RATIO', '0.5'))
ROOT = Path(os.environ.get('DT_OUTPUT_DIR', Path(__file__).resolve().parent))
DB = ROOT / 'services.sqlite'
FIELDS = (
    '+firstSeenTms,+lastSeenTms,+tags,+managementZones,'
    '+properties.serviceTechnologyTypes'
)


def stamp(ms):
    if ms is None:
        return ''
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='seconds')


def fetch_all():
    if not BASE.startswith('https://') or not TOKEN:
        raise ValueError(
            'Set DT_URL (https://...) and DT_API_TOKEN environment variables'
        )
    session = requests.Session()
    session.headers.update({
        'Authorization': f'Api-Token {TOKEN}',
        'Accept': 'application/json',
    })
    url = BASE + '/api/v2/entities'
    params = {
        'entitySelector': 'type(SERVICE)',
        'from': f'now-{DAYS}d',
        'pageSize': 500,
        'fields': FIELDS,
    }
    entities = []
    seen_cursors = set()
    expected_total = None
    for _ in range(10000):
        for retry in range(5):
            try:
                res = session.get(
                    url, params=params, timeout=(10, 45), allow_redirects=False
                )
                if res.status_code in (429, 500, 502, 503, 504) and retry < 4:
                    delay = res.headers.get('Retry-After', '')
                    time.sleep(min(int(delay) if delay.isdecimal() else 2 ** retry, 30))
                    continue
                res.raise_for_status()
                data = res.json()
                break
            except requests.RequestException:
                if retry == 4:
                    raise
                time.sleep(2 ** retry)
        if not isinstance(data, dict) or not isinstance(data.get('entities'), list):
            raise RuntimeError('Unexpected Dynatrace API JSON: missing entities array')
        if expected_total is None:
            expected_total = data.get('totalCount')
        entities.extend(data['entities'])
        cursor = data.get('nextPageKey')
        if not cursor:
            break
        if cursor in seen_cursors:
            raise RuntimeError('Repeated API pagination cursor')
        seen_cursors.add(cursor)
        params = {'nextPageKey': cursor}  # API requires no other params after page 1
    else:
        raise RuntimeError('Too many pages returned by API')
    if expected_total is not None and expected_total != len(entities):
        raise RuntimeError(
            f'Incomplete response: expected {expected_total}, got {len(entities)}'
        )
    ids = [x['entityId'] for x in entities]
    if any(not x.startswith('SERVICE-') for x in ids):
        raise RuntimeError('Unexpected entity ID: aborting database update')
    if len(ids) != len(set(ids)):
        raise RuntimeError('Duplicate entityId from API; no database changes saved')
    return entities


def init(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS services (
            service_id TEXT PRIMARY KEY, service_name TEXT NOT NULL,
            technology TEXT, management_zones TEXT, tags TEXT,
            dt_first_seen TEXT, dt_last_seen TEXT,
            registry_first_seen TEXT NOT NULL, registry_last_seen TEXT NOT NULL,
            missed_runs INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at TEXT NOT NULL, service_id TEXT NOT NULL,
            event_type TEXT NOT NULL, details TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS runs (
            observed_at TEXT PRIMARY KEY, total_found INTEGER NOT NULL,
            new_services INTEGER NOT NULL, absent_services INTEGER NOT NULL
        );
    ''')


def fmt_tags(tags):
    return '; '.join(sorted(t.get('stringRepresentation') or
               (t.get('key', '') + ':' + t.get('value', '')).strip(':') for t in tags))


def export(conn, name, query):
    cur = conn.execute(query)
    # Atomic CSV replacement avoids half-written reports after an I/O failure.
    with tempfile.NamedTemporaryFile('w', encoding='utf-8-sig', newline='',
                                     dir=ROOT, prefix='.export-', delete=False) as f:
        temp_name = f.name
        try:
            writer = csv.writer(f, delimiter=';')
            writer.writerow([c[0] for c in cur.description])
            writer.writerows(cur)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
    os.replace(temp_name, ROOT / name)


def main():
    if not 1 <= DAYS <= 3650 or MISSING_LIMIT < 1 or not 0 <= MIN_RESULT_RATIO <= 1:
        raise ValueError(
            'Invalid DT_LOOKBACK_DAYS, DT_MISSING_RUNS or DT_MIN_RESULT_RATIO'
        )
    entities = fetch_all()  # fetch complete list BEFORE touching SQLite
    # An unexpectedly empty response could result from scope/timeframe changes.
    # Reject it rather than classify every historical service as absent.
    if not entities:
        raise RuntimeError('Zero Services returned: refusing to modify inventory')
    ROOT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    conn = sqlite3.connect(DB)
    new_count = 0
    absent_count = 0
    with conn:
        init(conn)
        previous_size = conn.execute(
            'SELECT total_found FROM runs ORDER BY observed_at DESC LIMIT 1'
        ).fetchone()
        if previous_size and len(entities) < previous_size[0] * MIN_RESULT_RATIO:
            raise RuntimeError(
                'Abrupt drop in service count: refusing to update registry; '
                'check API scope'
            )
        existing = {
            r[0]: (r[1], r[2])
            for r in conn.execute(
                'SELECT service_id, service_name, status FROM services'
            )
        }
        found = set()
        for ent in entities:
            sid = ent['entityId']
            name = ent.get('displayName', sid)
            found.add(sid)
            props = ent.get('properties') or {}
            fields = (name,
                      '; '.join(props.get('serviceTechnologyTypes') or []),
                      '; '.join(sorted(
                          m.get('name', '') for m in ent.get('managementZones', [])
                      )),
                      fmt_tags(ent.get('tags') or []),
                      stamp(ent.get('firstSeenTms')), stamp(ent.get('lastSeenTms')))
            if sid not in existing:
                sql = '''
                    INSERT INTO services (
                        service_id, service_name, technology, management_zones,
                        tags, dt_first_seen, dt_last_seen, registry_first_seen,
                        registry_last_seen, missed_runs, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'OBSERVED')
                '''
                conn.execute(sql, (sid, *fields, now, now))
                conn.execute(
                    'INSERT INTO events(observed_at,service_id,event_type) '
                    'VALUES (?, ?, ?)', (now, sid, 'NEW')
                )
                new_count += 1
            else:
                old_name, old_status = existing[sid]
                sql = '''
                    UPDATE services SET
                        service_name=?, technology=?, management_zones=?,
                        tags=?, dt_first_seen=?, dt_last_seen=?,
                        registry_last_seen=?, missed_runs=0, status='OBSERVED'
                    WHERE service_id=?
                '''
                conn.execute(sql, (*fields, now, sid))
                if old_name != name:
                    conn.execute(
                        'INSERT INTO events(observed_at,service_id,event_type,details) '
                        'VALUES (?,?,?,?)',
                        (now, sid, 'RENAMED', old_name + ' -> ' + name),
                    )
                if old_status != 'OBSERVED':
                    conn.execute(
                        'INSERT INTO events(observed_at,service_id,event_type) '
                        'VALUES (?,?,?)', (now, sid, 'RETURNED')
                    )
        for sid, (_, old_status) in existing.items():
            if sid not in found:
                sql = '''
                    UPDATE services SET missed_runs=missed_runs+1,
                        status=CASE
                            WHEN missed_runs+1>=? THEN 'NOT_OBSERVED'
                            ELSE 'PENDING'
                        END
                    WHERE service_id=?
                '''
                conn.execute(sql, (MISSING_LIMIT, sid))
                new_status = conn.execute(
                    'SELECT status FROM services WHERE service_id=?', (sid,)
                ).fetchone()[0]
                if old_status != 'NOT_OBSERVED' and new_status == 'NOT_OBSERVED':
                    absent_count += 1
                    conn.execute(
                        'INSERT INTO events(observed_at,service_id,event_type) '
                        'VALUES (?,?,?)', (now, sid, 'NOT_OBSERVED')
                    )
        conn.execute(
            'INSERT INTO runs VALUES (?, ?, ?, ?)',
            (now, len(entities), new_count, absent_count),
        )
    columns = (
        'service_id,service_name,technology,management_zones,tags,'
        'dt_first_seen,dt_last_seen,registry_first_seen,'
        'registry_last_seen,missed_runs,status'
    )
    export(
        conn, 'services_current.csv',
        f'SELECT {columns} FROM services ORDER BY service_name',
    )
    export(
        conn, 'services_changes.csv',
        'SELECT observed_at,service_id,event_type,details '
        'FROM events ORDER BY observed_at DESC,id DESC',
    )
    export(conn, 'services_not_observed.csv', f'''SELECT {columns} FROM services
          WHERE status!='OBSERVED' ORDER BY missed_runs DESC,service_name''')
    export(conn, 'sync_history.csv', 'SELECT * FROM runs ORDER BY observed_at DESC')
    conn.close()
    print(
        f'OK: {len(entities)} observed, {new_count} new, '
        f'{absent_count} newly not-observed; outputs: {ROOT}'
    )


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
