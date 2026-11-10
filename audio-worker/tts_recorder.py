#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SB_TTS_20261110 — Bo thu tieng cho CONG CU "Chuyen van ban thanh giong
noi" cua SAOBAY, chay tren GitHub Actions (workflow tts.yml; neu workflow
rieng chua co thi recorder.py goi truc tiep main() cua module nay trong
cung luot chay record.yml).

GIAO THUC HANG CHO:
- Phieu nam o R2 data/tts-tickets/<id>.json do trang cong cu
  (tools/tts.html) ghi qua POST /api/v2/teacher/upload:
  {id, uid, name, text, voice: "hoaimy"|"namminh", rate: "-20%".." +20%",
   status: "pending", audio_key: "data/tts-audio/<id>.mp3",
   created_at, done_at, error, parts, duration_sec}
- Bo thu quet phieu status == "pending" (cu nhat truoc), nhan phieu bang
  cach lat status = "recording", thu bang Microsoft Edge TTS qua
  elearn/edge_tts_raw.py voi DUNG chuoi luat doc cua Lop hoc Co Sao
  (elearn/gen_pack_audio.py: so am doc "am", viet tat, don vi, phan so,
  goc...) — cung mot chuoi spoken_text() nhu recorder.py cua Module 14.
- Giong: hoaimy -> vi-VN-HoaiMyNeural; namminh -> vi-VN-NamMinhNeural
  (hai giong dat theo TEN do thay chot 22:26 10/11/2026; khong dung ten
  "Mau 1/Mau 3" trong cong cu nay). Toc do lay theo phieu, mac dinh 0%,
  kep trong khoang -20%..+20%.
- Van ban dai duoc cat theo CAU thanh tung doan <= 700 ky tu, thu tung
  doan roi noi lai thanh MOT file MP3 (chen 0,3s im lang giua cac doan)
  tai dung khoa audio_key cua phieu; xong lat status = "done" kem
  done_at + duration_sec. Loi thi lat status = "error" kem thong diep
  ngan. Phieu "recording" treo qua 20 phut bi danh dau error de nguoi
  dung gui lai.
- Hang cho tach rieng data/tts-tickets/ nen bo thu CUC BO
  (tools/mod14_audio_recorder.py, chi doc data/mod14-drafts/) khong bao
  gio cham vao phieu cua cong cu nay.
"""
import html as htmlmod
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

import edge_tts  # noqa: F401 — requirements.txt cai san

BASE = os.path.dirname(os.path.abspath(__file__))
LOGF = os.environ.get('TTS_RECORDER_LOG') or os.path.join(BASE, 'tts_recorder.log')

TICKET_PREFIX = 'data/tts-tickets/'
MAX_TEXT = 1500          # gioi han ky tu moi phieu (trang cong cu cung ep muc nay)
CHUNK = 700              # moi doan thu <= 700 ky tu, cat theo cau
MAX_TICKETS_PER_RUN = 10
STALE_RECORDING_SEC = 20 * 60

VOICES = {
    'hoaimy': 'vi-VN-HoaiMyNeural',
    'namminh': 'vi-VN-NamMinhNeural',
}
DEFAULT_VOICE = 'hoaimy'

try:
    import requests  # noqa: F401
except ImportError:
    import types
    _stub = types.ModuleType('requests')

    class _DummySession(object):
        def __init__(self):
            self.headers = {}

        def get(self, *a, **k):
            raise RuntimeError('requests stub: duong Google TTS cu khong dung trong tts_recorder')

        def post(self, *a, **k):
            raise RuntimeError('requests stub: duong Google TTS cu khong dung trong tts_recorder')

    _stub.Session = _DummySession
    sys.modules['requests'] = _stub

sys.path.insert(0, os.path.join(BASE, 'elearn'))
import edge_tts_raw  # noqa: E402

_spec = importlib.util.spec_from_file_location('gpa', os.path.join(BASE, 'elearn/gen_pack_audio.py'))
gpa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gpa)


def log(msg):
    line = time.strftime('%Y-%m-%d %H:%M:%S') + ' TTS ' + msg
    print(line, flush=True)
    try:
        with open(LOGF, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def spoken_text(raw):
    """Chuoi luat doc Y HET recorder.py (Module 14) / gen_pack_audio."""
    t = str(raw or '')
    t = htmlmod.unescape(t)
    t = gpa.array_to_words(t)
    t = t.replace('<', ' nhỏ hơn ')
    t = gpa.expand_abbr(gpa.unit_to_words(gpa.abs_to_words(
        gpa.minus_to_words(gpa.frac_to_words(gpa.struct_to_words(gpa.name_pron(t)))))))
    t = gpa.angle_to_words(t)
    t = gpa.strip_parens_spoken(t)
    return ' '.join(t.split())


# ===== Lop truy cap R2 bang boto3 (cung khuon recorder.py) =====
import boto3  # noqa: E402

BUCKET = os.environ.get('R2_BUCKET') or 'saobay-data'
_s3 = None


def _s3c():
    global _s3
    if _s3 is None:
        _s3 = boto3.client(
            's3',
            endpoint_url=os.environ['R2_ENDPOINT'],
            aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
            aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
            region_name='auto')
    return _s3


def r2_list(prefix):
    try:
        keys = []
        paginator = _s3c().get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get('Contents') or []:
                keys.append(obj['Key'])
        return keys
    except Exception as e:
        log('R2 LIST LOI %s: %s' % (prefix, str(e)[:200]))
        return []


def r2_get_json(key):
    try:
        resp = _s3c().get_object(Bucket=BUCKET, Key=key)
        return json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return None


def r2_put_json(key, obj):
    try:
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        _s3c().put_object(Bucket=BUCKET, Key=key, Body=data,
                          ContentType='application/json; charset=utf-8')
        return True
    except Exception:
        return False


def r2_put_file(key, path, ctype):
    try:
        with open(path, 'rb') as f:
            data = f.read()
        _s3c().put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=ctype)
        return True
    except Exception:
        return False


def mp3_ok(path):
    if not os.path.exists(path) or os.path.getsize(path) <= 2000:
        return False
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'csv=p=0', path], capture_output=True, text=True, timeout=30)
        return float((r.stdout or '0').strip() or 0) > 0
    except Exception:
        with open(path, 'rb') as f:
            head = f.read(3)
        return head[:3] == b'ID3' or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)


def mp3_duration(path):
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'csv=p=0', path], capture_output=True, text=True, timeout=30)
        return round(float((r.stdout or '0').strip() or 0), 1)
    except Exception:
        return None


def clamp_rate(rate):
    """Toc do cua phieu: chuoi dang '-20%'..'+20%'; sai/lech -> '0%'."""
    m = re.match(r'^([+-]?\d{1,3})%$', str(rate or '').strip())
    if not m:
        return '+0%'
    v = max(-20, min(20, int(m.group(1))))
    return ('+%d%%' % v) if v >= 0 else ('%d%%' % v)


def split_chunks(text, limit=CHUNK):
    """Cat van ban theo cau, gop thanh cac doan <= limit ky tu.
    Cau dai hon limit thi cat tiep tai dau phay/khoang trang gan nhat."""
    t = ' '.join(str(text or '').split())
    if not t:
        return []
    sents = re.split(r'(?<=[.!?…])\s+', t)
    pieces = []
    for s in sents:
        s = s.strip()
        while len(s) > limit:
            cut = max(s.rfind(', ', 0, limit), s.rfind(' ', 0, limit))
            if cut < limit // 3:
                cut = limit
            pieces.append(s[:cut].strip())
            s = s[cut:].strip()
        if s:
            pieces.append(s)
    chunks = []
    cur = ''
    for p in pieces:
        if cur and len(cur) + 1 + len(p) > limit:
            chunks.append(cur)
            cur = p
        else:
            cur = (cur + ' ' + p) if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def join_mp3(parts, out):
    """Noi cac doan MP3 thanh 1 file, chen 0,3s im lang giua cac doan.
    ffmpeg co san tren runner; loi ffmpeg thi noi nhi phan du phong."""
    if len(parts) == 1:
        with open(parts[0], 'rb') as f:
            data = f.read()
        with open(out, 'wb') as f:
            f.write(data)
        return True
    sil = out + '.sil.mp3'
    try:
        r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
                            '-i', 'anullsrc=r=24000:cl=mono', '-t', '0.3',
                            '-codec:a', 'libmp3lame', '-q:a', '4', sil],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and os.path.exists(sil):
            lst = out + '.list.txt'
            with open(lst, 'w', encoding='utf-8') as f:
                for i, p in enumerate(parts):
                    if i:
                        f.write("file '%s'\n" % os.path.abspath(sil))
                    f.write("file '%s'\n" % os.path.abspath(p))
            r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0',
                                '-i', lst, '-codec:a', 'libmp3lame', '-q:a', '4', out],
                               capture_output=True, text=True, timeout=120)
            try:
                os.remove(lst)
                os.remove(sil)
            except Exception:
                pass
            if r.returncode == 0 and mp3_ok(out):
                return True
    except Exception:
        pass
    try:
        with open(out, 'wb') as f:
            for p in parts:
                with open(p, 'rb') as pf:
                    f.write(pf.read())
        return mp3_ok(out)
    except Exception:
        return False


def process_ticket(key, tk):
    tid = str(tk.get('id') or os.path.basename(key)[:-5])
    text = str(tk.get('text') or '')
    truncated = False
    if len(text) > MAX_TEXT:
        cut = text[:MAX_TEXT]
        m = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '), cut.rfind('\n'))
        text = (cut[:m + 1] if m > MAX_TEXT // 2 else cut).strip()
        truncated = True
    voice_name = str(tk.get('voice') or DEFAULT_VOICE).strip().lower()
    voice = VOICES.get(voice_name, VOICES[DEFAULT_VOICE])
    rate = clamp_rate(tk.get('rate'))
    audio_key = str(tk.get('audio_key') or ('data/tts-audio/' + tid + '.mp3'))

    # Nhan phieu (lat recording) — tranh hai luot chay cung thu mot phieu.
    tk['status'] = 'recording'
    tk['claimed_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    if not r2_put_json(key, tk):
        log('KHONG NHAN DUOC PHIEU %s' % tid)
        return False

    def fail(msg):
        fresh = r2_get_json(key)
        if isinstance(fresh, dict):
            fresh['status'] = 'error'
            fresh['error'] = str(msg)[:200]
            fresh['done_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            r2_put_json(key, fresh)
        log('LOI PHIEU %s: %s' % (tid, msg))

    spk = spoken_text(text)
    if not spk:
        fail('Van ban rong sau khi chuan hoa')
        return False
    chunks = split_chunks(spk)
    if not chunks:
        fail('Khong cat duoc doan doc')
        return False
    parts = []
    try:
        for i, ch in enumerate(chunks):
            out = '/tmp/tts_%s_%d.mp3' % (tid, i)
            try:
                n = edge_tts_raw.synth(ch, voice, rate, out)
            except Exception as e:
                fail('Thu loi doan %d: %s' % (i + 1, str(e)[:150]))
                return False
            if not n or not mp3_ok(out):
                fail('Doan %d khong ra tieng hop le' % (i + 1))
                return False
            parts.append(out)
        final = '/tmp/tts_%s.mp3' % tid
        if not join_mp3(parts, final) or not mp3_ok(final):
            fail('Noi file loi')
            return False
        if not r2_put_file(audio_key, final, 'audio/mpeg'):
            fail('Day MP3 len R2 loi')
            return False
        dur = mp3_duration(final)
        fresh = r2_get_json(key)
        if not isinstance(fresh, dict):
            fresh = tk
        fresh['status'] = 'done'
        fresh['error'] = None
        fresh['audio_key'] = audio_key
        fresh['parts'] = len(parts)
        fresh['duration_sec'] = dur
        if truncated:
            fresh['truncated'] = True
        fresh['done_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        if not r2_put_json(key, fresh):
            log('LAT DONE LOI %s (MP3 da len R2)' % tid)
            return False
        log('XONG %s: %d doan, %s giay, giong %s %s -> %s'
            % (tid, len(parts), dur, voice_name, rate, audio_key))
        return True
    finally:
        for p in parts:
            try:
                os.remove(p)
            except Exception:
                pass
        try:
            os.remove('/tmp/tts_%s.mp3' % tid)
        except Exception:
            pass


def main():
    only = (os.environ.get('TTS_ONLY_ID') or '').strip()
    keys = [k for k in r2_list(TICKET_PREFIX) if k.endswith('.json')]
    tickets = []
    now = time.time()
    for k in keys:
        tk = r2_get_json(k)
        if not isinstance(tk, dict):
            continue
        st = tk.get('status')
        if st == 'pending':
            tickets.append((str(tk.get('created_at') or ''), k, tk))
        elif st == 'recording':
            # Phieu treo qua lau (runner chet giua chung) -> dong bang loi.
            ca = str(tk.get('claimed_at') or '')
            try:
                ts = time.mktime(time.strptime(ca, '%Y-%m-%dT%H:%M:%SZ')) - time.timezone
            except Exception:
                ts = 0
            if ts and now - ts > STALE_RECORDING_SEC:
                tk['status'] = 'error'
                tk['error'] = 'Qua thoi gian cho — hay gui lai'
                tk['done_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                r2_put_json(k, tk)
                log('DONG PHIEU TREO %s' % k)
    tickets.sort(key=lambda x: x[0])
    if only:
        tickets = [t for t in tickets if t[2].get('id') == only or only in t[1]]
    done = 0
    errs = []
    for _ca, k, tk in tickets[:MAX_TICKETS_PER_RUN]:
        try:
            if process_ticket(k, tk):
                done += 1
        except Exception as e:
            errs.append('%s: %s' % (k, str(e)[:150]))
            log('LOI NGOAI LE %s: %s' % (k, e))
    try:
        summary = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                   'runner': 'github-actions', 'pending_seen': len(tickets),
                   'done': done, 'errors': errs[:10]}
        _s3c().put_object(Bucket=BUCKET, Key='data/gh-audio-worker/tts-last-run.json',
                          Body=json.dumps(summary, ensure_ascii=False).encode('utf-8'),
                          ContentType='application/json; charset=utf-8')
    except Exception:
        pass
    if tickets:
        log('XONG LUOT TTS: %d/%d phieu' % (done, min(len(tickets), MAX_TICKETS_PER_RUN)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
