#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SB_GHRECORDER_20261110 — Ban GitHub Actions cua bo thu tieng TEN hoc
sinh (ban goc tren may ao: ~/workspace/tools/name_audio_recorder.py,
SB_NAMEAUDIO_20261109). Giu NGUYEN giao thuc kho ten duy nhat tren R2:

  - Index:  data/classroom/name-audio.json  {names, durations, ...}
  - File:   assets/classroom/names/<slug>.mp3
  - Khoa: thu 2-tu-cuoi truoc, khong co thi tu-cuoi; hoc sinh da co
    tieng khi 1 trong 2 khoa nam trong index; ten moi thu bang khoa
    TU CUOI; chuan hoa Chu-dau-hoa tung tu nhu ban goc.
  - Slug: bo dau -> chu thuong -> noi '-'; trung slug khac khoa thi
    cap -2, -3... theo dung index hien tai.
  - Giong MAU 3 (vi-VN-HoaiMyNeural, rate -20%, pitch +0Hz mac dinh
    cua edge_tts_raw) + CAT im lang hai dau (silencedetect -50dB, le
    dau 0,10s, le duoi 0,25s, ma hoa lai 24kHz mono 48kbit) nhu ban goc.
  - Ghi index kieu CHI THEM khoa chua co tren ban index doc tuoi —
    day la co che chong trung voi ban cuc bo dang chay song song:
    ca hai ben deu idempotent, khong bao gio thu lai khoa da co.

Khac biet theo moi truong chay (chi o tang ha tang, khong doi giao
thuc): R2 qua boto3 (secrets R2_*), khong re-exec venv, khong khoa
file /tmp (GitHub khoa bang nhom concurrency cua workflow). Nguon hoc
sinh: (1) ban sao dataD1/users.json tren R2 nhu ban goc; (2) truy van
D1 qua REST API khi co secret CF_API_TOKEN — tuong duong nguon ban
chup D1 day du ma ban cuc bo nhan qua --extra-users; (3) --extra-users
van ho tro khi chay tay de kiem thu. Thong ke totalStudents/
coveredByName chi nang theo nguon dong hoc sinh nhat nhu ban goc.
"""
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, 'elearn'))
import edge_tts_raw  # noqa: E402

import boto3  # noqa: E402

try:
    import requests  # noqa: E402
except Exception:  # pragma: no cover
    requests = None

LOGF = os.environ.get('NAME_RECORDER_LOG') or os.path.join(BASE, 'name-recorder.log')
VOICE = 'vi-VN-HoaiMyNeural'   # MAU 3 — Hoai My cham rai (y het ban goc)
RATE = '-20%'
INDEX_KEY = 'data/classroom/name-audio.json'
USERS_KEY = 'dataD1/users.json'
NAMES_DIR = 'assets/classroom/names/'
BUCKET = os.environ.get('R2_BUCKET') or 'saobay-data'

_S3 = None


def _s3c():
    global _S3
    if _S3 is None:
        _S3 = boto3.client(
            's3',
            endpoint_url=os.environ['R2_ENDPOINT'],
            region_name='auto',
            aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
            aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'])
    return _S3


def log(msg):
    line = time.strftime('%Y-%m-%d %H:%M:%S') + ' ' + msg
    print(line, flush=True)
    try:
        with open(LOGF, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def r2_get_json(key):
    try:
        r = _s3c().get_object(Bucket=BUCKET, Key=key)
        return json.loads(r['Body'].read().decode('utf-8'))
    except Exception:
        return None


def r2_put_json(key, obj):
    try:
        _s3c().put_object(Bucket=BUCKET, Key=key,
                          Body=json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                          ContentType='application/json; charset=utf-8')
        return True
    except Exception:
        return False


def r2_put_file(key, path):
    try:
        with open(path, 'rb') as f:
            _s3c().put_object(Bucket=BUCKET, Key=key, Body=f.read(),
                              ContentType='audio/mpeg')
        return True
    except Exception:
        return False


def mp3_duration(path):
    """Thoi luong MP3 bang ffprobe; None neu file khong hop le.
    Nguong nhe nhu ban goc vi clip ten rat ngan (~0,5-1,5s)."""
    try:
        if not os.path.exists(path) or os.path.getsize(path) <= 1200:
            return None
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'csv=p=0', path], capture_output=True, text=True, timeout=30)
        d = float((r.stdout or '0').strip() or 0)
        return d if d > 0.15 else None
    except Exception:
        return None


def speech_bounds(path):
    """(diem bat dau, diem ket thuc) cua phan CO TIENG, theo
    silencedetect -50dB; None neu do loi."""
    total = mp3_duration(path)
    if total is None:
        return None
    try:
        r = subprocess.run(['ffmpeg', '-i', path, '-af', 'silencedetect=noise=-50dB:d=0.1',
                            '-f', 'null', '-'], capture_output=True, text=True, timeout=60)
        events = re.findall(r'silence_(start|end): ([0-9.]+)', r.stderr or '')
        if not events:
            return (0.0, total)
        start = 0.0
        if events[0][0] == 'end':
            start = float(events[0][1])
        end = total
        if events[-1][0] == 'end' and abs(float(events[-1][1]) - total) < 0.05:
            for kind, val in reversed(events[:-1]):
                if kind == 'start':
                    end = float(val)
                    break
        return (start, end)
    except Exception:
        return None


def trim_silence(src, dst):
    """Cat im lang hai dau, giu le dau 0,10s + duoi 0,25s nhu clip cu
    trong kho; ma hoa lai dung dinh dang kho (24kHz, mono, 48kbit)."""
    b = speech_bounds(src)
    if b is None:
        return False
    start, end = b
    total = mp3_duration(src) or 0
    a = max(0.0, start - 0.10)
    z = min(total, end + 0.25)
    if z - a < 0.2:
        return False
    try:
        r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', src,
                            '-ss', '%.3f' % a, '-to', '%.3f' % z,
                            '-ar', '24000', '-ac', '1', '-b:a', '48k', dst],
                           capture_output=True, text=True, timeout=60)
        return r.returncode == 0 and mp3_duration(dst) is not None
    except Exception:
        return False


def norm_word(w):
    if w.isupper() or w.islower():
        return w.capitalize()
    return w


def norm_name(raw):
    s = ' '.join(str(raw or '').split())
    if not s:
        return ''
    return ' '.join(norm_word(w) for w in s.split(' '))


def name_keys(full):
    parts = full.split(' ')
    k1 = parts[-1] if parts else ''
    k2 = ' '.join(parts[-2:]) if len(parts) >= 2 else ''
    return k2, k1


def slugify(key):
    s = unicodedata.normalize('NFD', key)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    s = s.replace('đ', 'd').replace('Đ', 'D')
    s = s.lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


def d1_student_names():
    """Nguon D1 day du qua REST API (tuong duong ban chup D1 ma ban cuc
    bo nhan qua --extra-users). Chi chay khi co secret CF_API_TOKEN
    (quyen doc D1); khong co thi tra None va luot chay van di tiep
    bang nguon ban sao R2 nhu ban goc."""
    tok = os.environ.get('CF_API_TOKEN')
    if not tok or requests is None:
        return None
    acct = os.environ.get('CF_ACCOUNT_ID') or '00feaa3afee76d1cee65a794304b644e'
    db = os.environ.get('D1_DB_ID') or '232eb467-5ae6-4ca7-b960-e097463cce7e'
    try:
        names = []
        params = []
        while True:
            r = requests.post(
                'https://api.cloudflare.com/client/v4/accounts/%s/d1/database/%s/query'
                % (acct, db),
                headers={'Authorization': 'Bearer ' + tok,
                         'Content-Type': 'application/json'},
                json={'sql': "SELECT name FROM users WHERE role = 'student'",
                      'params': params}, timeout=60)
            data = r.json()
            if not data.get('success'):
                log('D1 REST tra loi: %s' % str(data.get('errors'))[:200])
                return None
            rows = data['result'][0]['results']
            for row in rows:
                if isinstance(row, dict) and row.get('name'):
                    names.append(str(row['name']))
            meta = data['result'][0].get('meta') or {}
            # D1 REST gioi han so dong moi truy van; ban chup cuc bo
            # dung --raw nen lay du. Neu bi cat, ghi log de biet.
            if meta.get('rows_read') and len(rows) < meta['rows_read']:
                log('D1 REST co the bi gioi han dong (%d/%d) — kiem tra lai'
                    % (len(rows), meta['rows_read']))
            break
        return names
    except Exception as e:
        log('D1 REST LOI: %s' % e)
        return None


def load_students(extra_path):
    """Danh sach ho ten hoc sinh THEO TUNG NGUON: [(nhan, [ten...]), ...].
    Giu rieng tung nguon vi cac nguon chong lap nhau — thong ke toan
    truong lay theo nguon dong hoc sinh nhat (y het ban goc)."""
    sources = []
    users = r2_get_json(USERS_KEY)
    if isinstance(users, dict) and isinstance(users.get('records'), dict):
        names = []
        for rec in users['records'].values():
            if isinstance(rec, dict) and rec.get('role') == 'student' and rec.get('name'):
                names.append(str(rec['name']))
        sources.append(('mirror users.json', names))
    else:
        log('R2 LIST LOI hoac sai cau truc %s — luot nay khong co nguon mirror' % USERS_KEY)
    d1 = d1_student_names()
    if d1 is not None:
        sources.append(('d1 rest', d1))
    if extra_path:
        try:
            with open(extra_path, encoding='utf-8') as f:
                extra = json.load(f)
            names = []
            if isinstance(extra, list):
                for it in extra:
                    if isinstance(it, str) and it.strip():
                        names.append(it)
                    elif isinstance(it, dict) and it.get('name'):
                        names.append(str(it['name']))
            sources.append(('extra ' + os.path.basename(extra_path), names))
        except Exception as e:
            log('LOI doc --extra-users %s: %s' % (extra_path, e))
    return sources


def main():
    extra_path = None
    args = sys.argv[1:]
    if '--extra-users' in args:
        i = args.index('--extra-users')
        if i + 1 < len(args):
            extra_path = args[i + 1]
    # Khong con khoa /tmp nhu ban cuc bo: GitHub khoa bang nhom
    # concurrency; chay song song voi ban cuc bo thi co che ghi index
    # chi-them-khoa-chua-co + khong bao gio thu lai khoa da co giu
    # ca hai ben khong lam trung viec (idempotent nhu ban goc).
    only = set()
    for part in (os.environ.get('NAMEAUDIO_ONLY') or '').split(','):
        part = part.strip()
        if part:
            only.add(norm_name(part))
    recorded = 0
    index = r2_get_json(INDEX_KEY)
    if not isinstance(index, dict) or not isinstance(index.get('names'), dict):
        log('LOI doc index %s — dung luot' % INDEX_KEY)
        return 0
    names_map = dict(index['names'])
    durations = dict(index.get('durations') or {})

    sources = load_students(extra_path)
    if only:
        sources = [(lbl, [n for n in ns if norm_name(n) in only]) for lbl, ns in sources]
        log('CHE DO THU GIOI HAN: chi cac ten %s' % ', '.join(sorted(only)))
    missing = {}
    skipped = {}
    src_stats = []
    for label, raw_names in sources:
        s_total = 0
        s_covered = 0
        s_missing = {}
        s_skipped = 0
        for raw in raw_names:
            full = norm_name(raw)
            if not full:
                continue
            s_total += 1
            k2, k1 = name_keys(full)
            if (k2 and k2 in names_map) or (k1 and k1 in names_map):
                s_covered += 1
            elif k1:
                if any(c.isalpha() for c in k1):
                    s_missing[k1] = s_missing.get(k1, 0) + 1
                    missing[k1] = missing.get(k1, 0) + 1
                else:
                    skipped[k1] = skipped.get(k1, 0) + 1
                    s_skipped += 1
        src_stats.append((label, s_total, s_covered, s_missing, s_skipped))
        log('NGUON %s: %d hoc sinh, da co tieng %d, thieu %d'
            % (label, s_total, s_covered, s_total - s_covered))
    for k in sorted(skipped):
        log('BO QUA khoa khong phai ten %r (%d hoc sinh)' % (k, skipped[k]))

    used_slug = {}
    for k, v in names_map.items():
        fn = str(v).split('/')[-1]
        if fn.endswith('.mp3'):
            used_slug[fn[:-4]] = k

    new_entries = {}
    for key in sorted(missing):
        base = slugify(key)
        if not base:
            log('BO QUA khoa %r: khong slug hoa duoc' % key)
            continue
        slug = base
        n = 2
        while slug in used_slug and used_slug[slug] != key:
            slug = '%s-%d' % (base, n)
            n += 1
        rel = NAMES_DIR + slug + '.mp3'
        out = '/tmp/nameaudio_%s.mp3' % slug
        try:
            nbytes = edge_tts_raw.synth(key, VOICE, RATE, out)
        except Exception as e:
            log('THU LOI ten %r: %s' % (key, e))
            continue
        if not nbytes or mp3_duration(out) is None:
            log('MP3 KHONG HOP LE ten %r (bytes=%s)' % (key, nbytes))
            continue
        trimmed = out + '.trim.mp3'
        if not trim_silence(out, trimmed):
            log('CAT TIENG LOI ten %r — bo qua luot nay, luot sau thu lai' % key)
            continue
        try:
            os.replace(trimmed, out)
        except Exception:
            out = trimmed
        dur = mp3_duration(out)
        nbytes = os.path.getsize(out)
        if not r2_put_file(rel, out):
            log('PUT R2 LOI ten %r -> %s' % (key, rel))
            continue
        used_slug[slug] = key
        new_entries[key] = (rel, round(dur, 2))
        recorded += 1
        log('READY ten %r -> %s (%d bytes, %.2fs, %s %s)' % (key, rel, nbytes, dur, VOICE, RATE))
        try:
            os.remove(out)
        except Exception:
            pass

    if new_entries:
        fresh = r2_get_json(INDEX_KEY)
        if not isinstance(fresh, dict) or not isinstance(fresh.get('names'), dict):
            log('LOI doc lai index — file tieng da len kho nhung CHUA cap nhat index (%d ten)' % len(new_entries))
        else:
            fnames = fresh['names']
            fdur = fresh.get('durations') if isinstance(fresh.get('durations'), dict) else {}
            added = 0
            for k, (rel, dur) in new_entries.items():
                if k not in fnames:
                    fnames[k] = rel
                    fdur[k] = dur
                    added += 1
            fresh['durations'] = fdur
            fresh['updatedAt'] = datetime.now(timezone(timedelta(hours=7))).isoformat()
            if src_stats:
                _lbl, p_total, p_covered, p_missing, _psk = max(src_stats, key=lambda s: s[1])
                if p_total >= int(fresh.get('totalStudents') or 0):
                    fresh['totalStudents'] = p_total
                    fresh['coveredByName'] = p_covered + sum(
                        p_missing[k] for k in new_entries if k in p_missing)
            if r2_put_json(INDEX_KEY, fresh):
                log('CAP NHAT INDEX: +%d khoa ten (tong %d khoa)' % (added, len(fnames)))
            else:
                log('PUT R2 LOI khi ghi index')
    if src_stats:
        _lbl, p_total, p_covered, p_missing, p_skipped = max(src_stats, key=lambda s: s[1])
        p_still = sum(v for k, v in p_missing.items() if k not in new_entries) + p_skipped
        log('XONG luot: thu moi %d khoa; nguon chinh %s: %d hoc sinh, phu %d -> %d, con thieu %d hoc sinh'
            % (recorded, _lbl, p_total, p_covered, p_total - p_still, p_still))
    try:
        summary = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                   'runner': 'github-actions', 'recorded': recorded,
                   'missingKeys': len(missing)}
        _s3c().put_object(Bucket=BUCKET, Key='data/gh-audio-worker/last-run-names.json',
                          Body=json.dumps(summary, ensure_ascii=False).encode('utf-8'),
                          ContentType='application/json; charset=utf-8')
    except Exception:
        pass
    try:
        lines = open(LOGF, encoding='utf-8').read().splitlines()
        if len(lines) > 300:
            open(LOGF, 'w', encoding='utf-8').write('\n'.join(lines[-300:]) + '\n')
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
