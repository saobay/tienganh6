#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SB_M14AUDIO_20261106 — Bo thu tieng loi dan Module 14 chay tren GitHub Actions.

Ban chuyen (port) cua tools/mod14_audio_recorder.py tren may ao Muse —
GIAO THUC HANG CHO GIONG HET: quet R2 data/mod14-drafts/<uid>/index.json;
voi moi ban ghi co muc audio dang 'pending': lay loi dan (uu tien
draft.boards[i].narr; khong co thi draft.boards[i].text), chay DUNG chuoi
luat doc cua Lop hoc Co Sao (elearn/gen_pack_audio.py) roi thu MP3 bang
backend Microsoft Edge TTS qua bo thu tho elearn/edge_tts_raw.py. Giong +
toc do lay THEO TUNG MUC (truong v/r do tab 14 ghi); muc cu khong co v/r
thi mac dinh MAU 3 chot cua thay: vi-VN-HoaiMyNeural, rate -20%, pitch
+0Hz (moc F0 co chuan 225–246 Hz). Thu xong ghi them kh = hash cua dung
doan text vua thu + v/r vua dung vao muc. MP3 dua len R2 dung khoa XAC
DINH trong ban ghi (data/mod14-audio/<draftId>/<i>.mp3 — ghi de), sau do
doc lai ban ghi TUOI va chi lat s='ready' khi hash loi dan con khop,
dong thoi cap nhat truong au trong index.json. Ngay khi mot clip thu
xong, neu ban ghi gan voi mot bai DA XUAT BAN thi GUONG clip vao kho
tieng cua bai dang chay + tang av cua goi (rao khop chu bat buoc —
SB_M14LIVEAUDIO_20261109), va xu ly ban ghi lenh sua-goi packJob
(SB_M14KTBC_AU_20261110) nhu ban goc.

Khac biet duy nhat so voi ban may ao (SB_GHRECORDER_20261110):
- Truy cap R2 bang boto3, credential doc tu bien moi truong
  R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_ENDPOINT / R2_BUCKET
  (GitHub Secrets) thay vi subprocess r2_s3.py + rclone.conf.
- Khong con file khoa /tmp rieng tung may: chong chay chong bang nhom
  concurrency cua GitHub Actions + cong hash khi lat ready tren chinh
  hang cho R2 (ban goc van giu duong cuc bo chay song song lam du phong).
- Them bo loc MOD14_ONLY_UID (chi thu cho cac uid liet ke, phuc vu
  kiem thu) va ghi tom tat moi luot vao R2
  data/gh-audio-worker/last-run.json de theo doi tu ben ngoai.
"""
import hashlib
import html as htmlmod
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

import edge_tts  # noqa: F401 — requirements.txt cai san (venv tren may ao khong con can)

BASE = os.path.dirname(os.path.abspath(__file__))
LOGF = os.environ.get('RECORDER_LOG') or os.path.join(BASE, 'recorder.log')
VOICE = 'vi-VN-HoaiMyNeural'   # MẪU 3 — Hoài My chậm rãi
RATE = '-20%'

try:
    import requests  # noqa: F401 — gen_pack_audio import ở đầu file
except ImportError:
    # Đường thu Edge TTS không dùng requests (chỉ đường Google cũ mới dùng);
    # stub để nạp được module luật đọc khi thieu thu vien.
    import types
    _stub = types.ModuleType('requests')

    class _DummySession(object):
        def __init__(self):
            self.headers = {}

        def get(self, *a, **k):
            raise RuntimeError('requests stub: duong Google TTS cu khong dung trong recorder')

        def post(self, *a, **k):
            raise RuntimeError('requests stub: duong Google TTS cu khong dung trong recorder')

    _stub.Session = _DummySession
    sys.modules['requests'] = _stub

sys.path.insert(0, os.path.join(BASE, 'elearn'))
import edge_tts_raw  # noqa: E402

# ===== SB_M14KTBC_AU_20261110: TIENG HOC SINH PHAN VAI (4 giong da duyet) =====
# Boc TTSConfig de ep pitch SSML tai nguon nhu work/kidvoice/gen_phanvai.py.
# Mac dinh _KID_FORCED = '+0Hz' (y het gia tri san xuat edge_tts_raw tu
# dung) nen moi duong thu giong co giu nguyen 100%; chi khi thu tieng HS
# moi doi pitch theo giong roi tra lai ngay sau moi clip.
_KID_FORCED = {'pitch': '+0Hz'}
try:
    from edge_tts.data_classes import TTSConfig as _SbRealCfg

    def _sb_kid_cfg(voice, rate, volume, pitch, boundary):
        return _SbRealCfg(voice, rate, volume, _KID_FORCED['pitch'], boundary)

    edge_tts_raw.TTSConfig = _sb_kid_cfg
except Exception:
    pass

# (pitch, giong, toc do, rubberband) — KHOP gen_phanvai + bulk-standard VOICES:
# a: HoaiMy -5% +45Hz · a2: HoaiMy -3% +85Hz · a3: NamMinh -5% +110Hz +
# rubberband x1.35 · a4: HoaiMy -8% +22Hz.
KID_VOICES = [
    ('+45Hz', 'vi-VN-HoaiMyNeural', '-5%', None),
    ('+85Hz', 'vi-VN-HoaiMyNeural', '-3%', None),
    ('+110Hz', 'vi-VN-NamMinhNeural', '-5%', 1.35),
    ('+22Hz', 'vi-VN-HoaiMyNeural', '-8%', None),
]

_spec = importlib.util.spec_from_file_location('gpa', os.path.join(BASE, 'elearn/gen_pack_audio.py'))
gpa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gpa)


def log(msg):
    line = time.strftime('%Y-%m-%d %H:%M:%S') + ' ' + msg
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOGF), exist_ok=True)
        with open(LOGF, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def spoken_text(raw):
    """Chuỗi luật đọc y hệt gen_pack_audio / mẫu thu #71 (kèm sửa dấu <)."""
    t = str(raw or '')
    # SB_ARRAYWORDS_20261109: gỡ thực thể HTML trước (&amp; -> &) rồi đọc
    # bảng array trước mọi lớp khác — chữ từ HTML mang &amp; mà không gỡ
    # thì lớp array không nhận ra dấu & ngăn cột; & thô còn sót thì SSML vỡ.
    t = htmlmod.unescape(t)
    t = gpa.array_to_words(t)
    t = t.replace('<', ' nhỏ hơn ')
    t = gpa.expand_abbr(gpa.unit_to_words(gpa.abs_to_words(
        gpa.minus_to_words(gpa.frac_to_words(gpa.struct_to_words(gpa.name_pron(t)))))))
    t = gpa.angle_to_words(t)
    t = gpa.strip_parens_spoken(t)
    return ' '.join(t.split())


# ===== SB_PAUSEFIX_20261109: NGAT GIUA CAC PHUONG AN KHI DOC CAU HOI =====
# Thay bao 18:59 09/11: doc cau KTBC "may cau a b c d hoi nhanh, khong
# ngat giua cac cau". Nguyen nhan da do bang WordBoundary tren clip
# that: loi doc cau hoi ghep "…A. <pa A>. B. <pa B>. …" — phuong an ket
# thuc bang chu cai dung mot minh (vd "…tan b.") thi Edge hieu "b. B."
# la viet tat nen doc LIEN, ngat = 0ms; phuong an ket bang tu thuong
# thi Edge tu ngat ~1,08s. Khong dong nhat + khong chac -> chuan hoa
# bang danh dau ngat THAT cua edge_tts_raw (PAUSE_MARK_S truoc phuong
# an A, PAUSE_MARK truoc B/C/D...): synth cat doan, cat im lang thua
# hai dau roi noi lai sao cho TONG quang ngat giua hai phuong an lien
# tiep = 0,70s va giua cau dan voi phuong an A = 0,45s.
# CHI ap cho tieng CAU HOI (qaudio + raudio) — loi bang
# va muc khong qua ham nay. Nhan dien ranh gioi: chu cai phuong an dung
# mot minh + dau cham, ngay sau dau ket cau (. ! ? …), va day chu cai
# phai tang dan lien tiep A->B->C...; lech thu tu/dieu kien thi bo qua
# ranh gioi do (an toan, khong chen nham vao giua cong thuc).
_OPT_BOUND = re.compile(r'\s+([A-H])\.\s+')


def question_pause_text(spk):
    t = str(spk or '')
    if ' A. ' not in t:
        return t
    out = []
    last = 0
    expect = 'A'
    for m in _OPT_BOUND.finditer(t):
        if m.group(1) != expect:
            continue
        prev = t[:m.start()].rstrip()
        if not prev or prev[-1] not in '.!?…':
            continue
        mark = edge_tts_raw.PAUSE_MARK_S if expect == 'A' else edge_tts_raw.PAUSE_MARK
        out.append(t[last:m.start()])
        out.append(' ' + mark + ' ')
        last = m.start()
        expect = chr(ord(expect) + 1)
        if expect > 'H':
            break
    if not out:
        return t
    out.append(t[last:])
    return ''.join(out)


# ===== SB_M14SYNC_20261107: BẢNG MỐC KHỚP TIẾNG TỪ BẢN THU THẬT =====
# Khung chữ trên bảng mang data-fxsync="<cụm từ trong lời đọc>"; khi thu,
# recorder lấy mốc từng từ (WordBoundary của Edge TTS qua synth_timed)
# của ĐÚNG đoạn spoken_text vừa thu, tìm vị trí bắt đầu của cụm từ trong
# dòng từ đã đọc rồi ghi kèm mục audio trường tm = {khoáCụmTừ: ms} (gắn
# với kh của chính bản thu đó — đổi chữ/giọng, bản thu mới ghi đè tm mới;
# phía web chỉ dùng tm khi kh khớp hash lời đọc hiện tại).
SYNC_PUNCT = '.,!?…:;\'"“”‘’«»()[]{}<>-–—/|@#$%^&*_+=~`'


def normalize_phrase_key(s):
    """Chuẩn hoá khoá cụm từ — PHẢI khớp từng chữ với sbMod14SyncKey()
    phía web (cùng bảng dấu câu SYNC_PUNCT, cùng thao tác: chữ thường ->
    bỏ dấu câu -> gộp khoảng trắng)."""
    s = str(s or '').lower()
    s = ''.join(c for c in s if c not in SYNC_PUNCT)
    return ' '.join(s.split())


def extract_sync_phrases(board_html):
    """Rút các cụm mốc data-fxsync trong html của bảng (bản lưu mang html
    đã qua sanitizer — thuộc tính còn nguyên trên .fxbox)."""
    if not board_html:
        return []
    out = []
    for v in re.findall(r'data-fxsync="([^"]*)"', str(board_html)):
        v = htmlmod.unescape(v).strip()
        if v and v not in out:
            out.append(v)
    return out


def _find_seq(stream, toks):
    """stream = [(từ đã chuẩn hoá, ms)], toks = [từ...]; trả ms của từ
    đầu tiên trong lần khớp đầu tiên, không khớp -> None."""
    if not toks or len(toks) > len(stream):
        return None
    for i in range(0, len(stream) - len(toks) + 1):
        if all(stream[i + j][0] == toks[j] for j in range(len(toks))):
            return stream[i][1]
    return None


def sync_word_stream(words):
    """Dòng từ đã chuẩn hoá của bản thu: [(từ, ms)] — dùng để tính tm
    ngay khi thu VÀ lưu kèm mục audio (trường tw) để phía web tự tra mốc
    cho cả những cụm GV gắn SAU khi đã thu (vd xoá khung cũ rồi ra lệnh
    AI tạo khung khớp tiếng mới): chỉ cần text/giọng/tốc độ không đổi
    (kh còn khớp) là mốc vẫn đúng, không phải thu lại."""
    stream = []
    for w in words or []:
        nw = normalize_phrase_key(w.get('w') or '')
        for tok in nw.split(' '):
            if tok:
                stream.append((tok, int(round(w.get('o', 0)))))
    return stream


def resolve_sync_tm(phrases, words):
    """phrases = [cụm mốc nguyên văn], words = [{'o','d','w'}] từ
    WordBoundary của bản thu. Thử khớp cụm nguyên văn trước, không được
    thì thử dạng đã qua luật đọc (spoken_text của riêng cụm — vd cụm có
    viết tắt/phân số). Cụm không khớp được thì bỏ qua (phía phát sẽ hiện
    khung lúc hết tiếng của bảng)."""
    stream = sync_word_stream(words)
    out = {}
    for ph in phrases or []:
        key = normalize_phrase_key(ph)
        if not key:
            continue
        cands = [key]
        try:
            k2 = normalize_phrase_key(spoken_text(ph))
            if k2 and k2 != key:
                cands.append(k2)
        except Exception:
            pass
        for cand in cands:
            ms = _find_seq(stream, cand.split(' '))
            if ms is not None:
                out[key] = int(round(ms))
                break
    return out


# ===== SB_GHRECORDER_20261110: LOP TRUY CAP R2 BANG boto3 =====
# Thay the lop subprocess r2_s3.py cua ban may ao (rclone.conf chi co
# tren may ao). Credential doc tu bien moi truong (GitHub Secrets):
# R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_ENDPOINT / R2_BUCKET.
# Ham r2() duoi day chi ho tro thao tac 'put' dung o cac diem goi cu
# (tra ve doi tuong co .returncode nhu subprocess.CompletedProcess).
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


class _PutResult(object):
    def __init__(self, rc):
        self.returncode = rc


def r2(*args):
    if args and args[0] == 'put':
        key, path = args[1], args[2]
        ctype = args[3] if len(args) > 3 else 'application/octet-stream'
        try:
            with open(path, 'rb') as f:
                data = f.read()
            _s3c().put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=ctype)
            return _PutResult(0)
        except Exception:
            return _PutResult(1)
    raise ValueError('r2: thao tac khong ho tro %r' % (args,))


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
                          ContentType='application/octet-stream')
        return True
    except Exception:
        return False


def mp3_ok(path):
    if not os.path.exists(path) or os.path.getsize(path) <= 3000:
        return False
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'csv=p=0', path], capture_output=True, text=True, timeout=30)
        return float((r.stdout or '0').strip() or 0) > 0
    except Exception:
        with open(path, 'rb') as f:
            head = f.read(3)
        return head[:3] == b'ID3' or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)


def audio_summary(draft):
    # SB_M14QAUDIO_20261107: tinh ca tieng cua bang (audio) lan tieng
    # cua cau (qaudio) — ban con cau dang cho thu thi index van 'pending'.
    au = draft.get('audio') if isinstance(draft, dict) else None
    qau = draft.get('qaudio') if isinstance(draft, dict) else None
    lau = draft.get('laudio') if isinstance(draft, dict) else None
    rau = draft.get('raudio') if isinstance(draft, dict) else None  # SB_M14KTBC_20261109: tieng cau KTBC
    # SB_M14KTBC_AU_20261110: tinh ca tieng rieng KTBC (giai thich /
    # nhanh sai / tieng HS / mo dau) vao trang thai chung cua ban ghi.
    extra = []
    reau = draft.get('reaudio') if isinstance(draft, dict) else None
    if isinstance(reau, list):
        extra.extend(x for x in reau if isinstance(x, dict))
    rwau = draft.get('rwaudio') if isinstance(draft, dict) else None
    if isinstance(rwau, list):
        for row in rwau:
            if isinstance(row, list):
                extra.extend(x for x in row if isinstance(x, dict))
    rkau = draft.get('rkaudio') if isinstance(draft, dict) else None
    if isinstance(rkau, list):
        for e in rkau:
            if isinstance(e, dict):
                vs = e.get('voices') if isinstance(e.get('voices'), list) else []
                k0 = next((v.get('k') for v in vs if isinstance(v, dict) and v.get('k')), None)
                extra.append({'k': k0, 's': e.get('s')})
    irau = draft.get('iraudio') if isinstance(draft, dict) else None
    if isinstance(irau, dict):
        extra.append(irau)
    lists = [x for x in (au, qau, lau, rau) if isinstance(x, list)]
    if extra:
        lists.append(extra)
    if not lists:
        return ''
    any_k = any(isinstance(a, dict) and a.get('k') for x in lists for a in x)
    pend = any(isinstance(a, dict) and a.get('k') and a.get('s') != 'ready' for x in lists for a in x)
    return '' if not any_k else ('pending' if pend else 'ready')


# ===== SB_M14LIVEAUDIO_20261109: GUONG CLIP VUA THU VAO BAI DANG CHAY =====
# Hash djb2 Y HET sbMod14TextHash phia web: h=5381; h=((h<<5)+h+ky tu)
# >>> 0 tren tung DON VI UTF-16; tra 'h' + h he 36. Loi doc hieu dung
# cua bang/muc trong BAN GHI duoc cat trang truoc khi bam (dung chuan
# ma phia web da dung khi tinh muc.h); loi trong GOI duoc bam nguyen
# van nhu da luu (goi luu ban da cat trang san).
def texthash(text):
    t = str(text or '')
    data = t.encode('utf-16-be', 'surrogatepass')
    h = 5381
    for i in range(0, len(data), 2):
        u = (data[i] << 8) | data[i + 1]
        h = ((h << 5) + h + u) & 0xFFFFFFFF
    if h == 0:
        return 'h0'
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
    s = ''
    while h:
        s = digits[h % 36] + s
        h //= 36
    return 'h' + s


def kid_hash_source(check):
    """Chuoi nguon bam hash cua tieng HS — TRANG SOAN va RECORDER dung
    CHUNG mot cong thuc tren dung object check da nung trong
    draft.review.checks: q + '\\n' + options.join('\\n') + '\\n' + answer.
    Doi bat ky truong nao (cau / phuong an / dap an) la hash doi -> ca
    4 giong tu xep thu lai."""
    if not isinstance(check, dict):
        return ''
    opts = check.get('options') or []
    ans = check.get('answer')
    return '%s\n%s\n%s' % (check.get('q') or '',
                           '\n'.join(str(o) for o in opts),
                           '' if ans is None else ans)


def kid_line(check):
    """Cau HS ao tra loi DUNG — khuon da duyet (bulk-standard
    student_line): 'Em thua co, em chon dap an X: <phuong an dung da
    qua luat doc> a.' (bo dau cham cuoi cua phuong an truoc khi noi)."""
    if not isinstance(check, dict):
        return ''
    opts = check.get('options') or []
    ai = check.get('answer')
    if not isinstance(ai, int) or isinstance(ai, bool) or ai < 0 or ai >= len(opts):
        return ''
    o = spoken_text(str(opts[ai] or '')).strip()
    if o.endswith('.'):
        o = o[:-1].strip()
    if not o:
        return ''
    return 'Em thưa cô, em chọn đáp án %s: %s ạ.' % ('ABCD'[ai], o)


def synth_kid(text, vi, out):
    """Thu 1 giong HS (vi 0..3 tuong ung a/a2/a3/a4). Tra so byte > 0
    neu clip hop le, 0 neu loi. Pitch ep tai nguon qua _KID_FORCED;
    giong a3 them rubberband x1.35 sau thu (nhu gen_phanvai)."""
    pitch, voice, rate, rb = KID_VOICES[vi]
    _KID_FORCED['pitch'] = pitch
    try:
        if rb:
            raw = out + '.raw.mp3'
            n = edge_tts_raw.synth(text, voice, rate, raw)
            if not n or not mp3_ok(raw):
                return 0
            r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', raw,
                                '-af', 'rubberband=pitch=%s' % rb,
                                '-codec:a', 'libmp3lame', '-q:a', '4', out],
                               capture_output=True, text=True, timeout=120)
            try:
                os.remove(raw)
            except Exception:
                pass
            if r.returncode != 0 or not mp3_ok(out):
                return 0
            return os.path.getsize(out)
        n = edge_tts_raw.synth(text, voice, rate, out)
        return n if (n and mp3_ok(out)) else 0
    finally:
        _KID_FORCED['pitch'] = '+0Hz'


# Tai khoan dac quyen xuat thang bai goc (khop Worker publish-elearn):
# kho 'admin' va uid cua thay. GV thuong chi guong vao BAN RIENG cua
# minh — tuyet doi khong cham bai goc cua nguoi khac.
PRIV_UIDS = ('admin', '1734760451')


def resolve_pack_key(uid, did, draft):
    """Khoa R2 cua goi bai DANG CHAY gan voi ban ghi nay, hoac None.
    srcPack co -> bai goc (dac quyen) / ban rieng cua GV (thuong);
    srcPack rong -> bai cua chinh ban ghi gv-<draftId>."""
    src = str(draft.get('srcPack') or '').strip()
    if src:
        if uid in PRIV_UIDS:
            return 'elearn-packs/' + src + '.json'
        return 'elearn-packs-var/' + uid + '/' + src + '.json'
    if did:
        return 'elearn-packs/gv-' + str(did) + '.json'
    return None


def _draft_board_text(boards, i):
    """Loi doc cua bang i dung nhu vong thu cua recorder (narr truoc,
    khong co narr thi text)."""
    if i >= len(boards) or not isinstance(boards[i], dict):
        return ''
    narr = boards[i].get('narr') or ''
    return narr if str(narr).strip() else (boards[i].get('text') or '')


def _draft_level_say(boards, bi, lv):
    if bi >= len(boards) or not isinstance(boards[bi], dict):
        return ''
    lvs = boards[bi].get('levels')
    if isinstance(lvs, list) and lv < len(lvs) and isinstance(lvs[lv], dict):
        return str(lvs[lv].get('say') or '')
    return ''


def mirror_targets(pack, draft, kind, idx, rec_hash):
    """Danh sach ten file dich trong kho audioBase cua goi cho mot clip
    vua thu — RONG neu rao khop chu khong cho qua. Doan/muc khop theo
    THU TU XUAT HIEN (lan xuat hien thu k trong ban ghi -> lan thu k
    trong goi) de hai bang trung loi nhau khong ghi de nham cho nhau."""
    nodes = pack.get('nodes') if isinstance(pack, dict) else None
    if not isinstance(nodes, list) or not rec_hash:
        return []
    boards = draft.get('boards') if isinstance(draft.get('boards'), list) else []
    if kind == 'board':
        matches = []
        for ni, nd in enumerate(nodes):
            if not isinstance(nd, dict):
                continue
            for si, sg in enumerate(nd.get('segments') or []):
                if isinstance(sg, dict) and texthash(sg.get('say') or '') == rec_hash:
                    matches.append('n%ds%d.mp3' % (ni, si))
        rank = sum(1 for j in range(min(idx, len(boards)))
                   if texthash(str(_draft_board_text(boards, j)).strip()) == rec_hash)
        return [matches[rank]] if rank < len(matches) else []
    if kind == 'level':
        bi, lv = divmod(idx, 4)
        key = 'L%d' % (lv + 1)
        matches = []
        for ni, nd in enumerate(nodes):
            if not isinstance(nd, dict):
                continue
            lvs = nd.get('levels')
            if isinstance(lvs, dict) and isinstance(lvs.get(key), dict):
                if texthash(lvs[key].get('say') or '') == rec_hash:
                    matches.append('n%dl%s.mp3' % (ni, key))
        rank = sum(1 for j in range(min(bi, len(boards)))
                   if texthash(_draft_level_say(boards, j, lv).strip()) == rec_hash)
        return [matches[rank]] if rank < len(matches) else []
    if kind == 'question':
        # Goi phai mang qaH do trinh soan ghi luc xuat (hash loi doc cua
        # tung cau) va con check tham chieu dung file nay — thieu mot
        # trong hai la khong guong (goi cu chua co qaH -> bo qua an toan).
        qaH = pack.get('qaH') if isinstance(pack, dict) else None
        if not isinstance(qaH, dict) or qaH.get(str(idx)) != rec_hash:
            return []
        want = 'q%d.mp3' % idx
        for nd in nodes:
            if not isinstance(nd, dict):
                continue
            for ck in (nd.get('checks') or []):
                if isinstance(ck, dict) and ck.get('audio') == want:
                    return [want]
        return []
    return []


def pack_fingerprint(pack):
    core = {k: v for k, v in pack.items() if k != 'av'}
    raw = json.dumps(core, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def r2_put_pack(key, pack):
    tmp = '/tmp/mod14rec_pack.json'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(pack, f, ensure_ascii=False)
    return r2('put', key, tmp, 'application/json; charset=utf-8').returncode == 0


def mirror_clip(uid, did, draft, kind, idx, entry, local_path):
    """Guong 1 clip vua thu xong vao kho tieng cua bai dang chay + tang
    av cua goi. Tra True neu co ghi. Khong bao gio nem loi ra ngoai
    (try_mirror bao boc) — guong hong khong duoc lam hong viec thu."""
    rec_hash = entry.get('h') if isinstance(entry, dict) else None
    if not rec_hash:
        return False
    pack_key = resolve_pack_key(uid, did, draft)
    if not pack_key:
        return False
    for _attempt in range(3):
        pack = r2_get_json(pack_key)
        if not isinstance(pack, dict) or not isinstance(pack.get('nodes'), list):
            return False  # bai chua xuat ban / khong con -> khong co bai dang chay
        base = str(pack.get('audioBase') or '')
        if not base:
            return False
        targets = mirror_targets(pack, draft, kind, idx, rec_hash)
        if not targets:
            return False  # rao khop chu: loi vua thu khong khop loi trong goi
        fp = pack_fingerprint(pack)
        ok = True
        for t in targets:
            if r2('put', base + t, local_path, 'audio/mpeg').returncode != 0:
                ok = False
        if not ok:
            log('GUONG PUT LOI %s %s %d -> %s' % (did, kind, idx, pack_key))
            return False
        fresh = r2_get_json(pack_key)
        if not isinstance(fresh, dict) or pack_fingerprint(fresh) != fp:
            continue  # goi vua bi ghi de (xuat ban lai) -> tinh lai tren ban tuoi
        fresh['av'] = int(time.time())
        if r2_put_pack(pack_key, fresh):
            log('GUONG %s %s %d -> %s%s (av=%d)' % (did, kind, idx, base, targets[0], fresh['av']))
            return True
    log('GUONG %s %s %d: goi thay doi lien tuc — da ghi file, chua tang av' % (did, kind, idx))
    return True


def try_mirror(uid, did, draft, kind, idx, entry, local_path):
    try:
        mirror_clip(uid, did, draft, kind, idx, entry, local_path)
    except Exception as e:
        log('GUONG LOI %s %s %d: %s' % (did, kind, idx, e))


# ===== SB_M14KTBC_AU_20261110: BAN GHI LENH SUA-GOI (packJob) =====
# Phien sua-goi KHONG co ban nhap: trang ghi mot ban ghi lenh dac biet
# (id 'pkj-<packId>', co co packJob) vao chinh kho ban luu cua GV qua
# API drafts/save that. entry.k cua lenh tro THANG vao kho tieng cua
# goi nen clip thu xong nam dung cho ngay; recorder con phai gan THAM
# CHIEU vao goi (audio/audioE/audioA/wrong[].audio, intro) — chi khi
# truong chu tuong ung trong goi con KHOP ban da thu (GV sua tiep sau
# khi xep thu thi ban moi thang, lenh cu khong duoc gan). Moi lan co
# ghi that vao goi thi tang av nhu mirror_clip.
def job_apply_to_pack(uid, did, draft, apply_fn):
    pack_key = resolve_pack_key(uid, did, draft)
    if not pack_key:
        return False
    for _attempt in range(3):
        pack = r2_get_json(pack_key)
        if not isinstance(pack, dict):
            return False
        fp = pack_fingerprint(pack)
        try:
            if not apply_fn(pack):
                return False
        except Exception as e:
            log('JOB APPLY LOI %s: %s' % (did, e))
            return False
        fresh = r2_get_json(pack_key)
        if not isinstance(fresh, dict) or pack_fingerprint(fresh) != fp:
            continue  # goi vua bi ghi de o noi khac -> tinh lai tren ban tuoi
        pack['av'] = int(time.time())
        if r2_put_pack(pack_key, pack):
            log('JOB->GOI %s -> %s (av=%d)' % (did, pack_key, pack['av']))
            return True
    log('JOB->GOI %s: goi thay doi lien tuc — bo qua lan nay' % did)
    return False


def _job_checks(pack):
    rv = pack.get('review') if isinstance(pack, dict) else None
    checks = rv.get('checks') if isinstance(rv, dict) else None
    return checks if isinstance(checks, list) else None


def job_set_review_field(uid, did, draft, j, field, value, guard_ck):
    """Gan 1 truong tieng cho check KTBC j trong goi dich. guard_ck =
    check DA NUNG trong ban ghi lenh; chi gan khi check trong goi con
    khop cac truong chu lien quan (so trong apply_fn)."""
    job_ck = None
    rv = draft.get('review') if isinstance(draft, dict) else None
    if isinstance(rv, dict) and isinstance(rv.get('checks'), list) and j < len(rv['checks']):
        job_ck = rv['checks'][j]

    def ap(pack):
        checks = _job_checks(pack)
        if checks is None or j >= len(checks) or not isinstance(checks[j], dict):
            return False
        ck = checks[j]
        if field == 'wrong':
            wi, val = value
            w = ck.get('wrong')
            if not isinstance(w, list) or wi >= len(w) or not isinstance(w[wi], dict):
                return False
            jw = job_ck.get('wrong') if isinstance(job_ck, dict) else None
            pw = w[wi].get('say') or ''
            jws = ''
            if isinstance(jw, list) and wi < len(jw) and isinstance(jw[wi], dict):
                jws = jw[wi].get('say') or ''
            if pw != jws:
                return False
            if w[wi].get('audio') == val:
                return False
            w[wi]['audio'] = val
            return True
        if not guard_ck(ck, job_ck):
            return False
        if ck.get(field) == value:
            return False
        ck[field] = value
        return True

    return job_apply_to_pack(uid, did, draft, ap)


def job_set_intro(uid, did, draft, say, fname):
    """Cap nhat review.intro cua goi dich sau khi thu lai clip mo dau
    KTBC: say = dung loi da thu (trang ghi trong ban ghi lenh), audio
    tro dung file vua ghi de. Phia trang luu goi sau do dung lai dung
    loi nay tu bang mo dau nen khong lech."""

    def ap(pack):
        rv = pack.get('review') if isinstance(pack, dict) else None
        if not isinstance(rv, dict):
            pack['review'] = {'intro': {'say': say, 'audio': fname}, 'checks': []}
            return True
        intro = rv.get('intro')
        if not isinstance(intro, dict):
            intro = {}
            rv['intro'] = intro
        if intro.get('say') == say and intro.get('audio') == fname:
            return False
        intro['say'] = say
        intro['audio'] = fname
        return True

    return job_apply_to_pack(uid, did, draft, ap)


def _guard_same_text(field):
    def g(ck, job_ck):
        if not isinstance(job_ck, dict):
            return False
        return (ck.get(field) or '') == (job_ck.get(field) or '')
    return g


def _guard_same_question(ck, job_ck):
    if not isinstance(job_ck, dict):
        return False
    return (ck.get('q') or '') == (job_ck.get('q') or '') \
        and (ck.get('options') or []) == (job_ck.get('options') or []) \
        and ck.get('answer') == job_ck.get('answer')


def _guard_same_kid(ck, job_ck):
    if not isinstance(job_ck, dict):
        return False
    return kid_hash_source(ck) == kid_hash_source(job_ck)


def entry_voice_rate(a):
    """Giọng + tốc độ của một mục audio; mục cũ thiếu v/r -> mặc định Mẫu 3."""
    if not isinstance(a, dict):
        return VOICE, RATE
    return (a.get('v') or VOICE), (a.get('r') or RATE)


def process_uid(uid):
    base = 'data/mod14-drafts/' + uid
    index = r2_get_json(base + '/index.json')
    if not isinstance(index, dict) or not isinstance(index.get('drafts'), list):
        return 0
    done = 0
    index_dirty = False
    for entry in index['drafts']:
        did = entry.get('id') if isinstance(entry, dict) else None
        if not did:
            continue
        draft = r2_get_json(base + '/' + did + '.json')
        if not isinstance(draft, dict):
            continue
        au = draft.get('audio')
        if not isinstance(au, list):
            au = []  # SB_M14QAUDIO_20261107: ban chi co tieng cau van phai chay tiep xuong vong qaudio
        boards = draft.get('boards') if isinstance(draft.get('boards'), list) else []
        for i, a in enumerate(au):
            if not isinstance(a, dict) or a.get('s') != 'pending' or not a.get('k'):
                continue
            text = ''
            if i < len(boards) and isinstance(boards[i], dict):
                # SB_M14AUDIOSET_20261107: ưu tiên LỜI ĐỌC RIÊNG (narr) của
                # bảng; không có narr thì text (text của bản lưu vốn đã là
                # lời đọc hiệu dụng do phía web ghi — hai đường khớp nhau).
                narr = boards[i].get('narr') or ''
                text = narr if str(narr).strip() else (boards[i].get('text') or '')
            if not text.strip():
                continue
            out = '/tmp/mod14rec_%s_%d.mp3' % (did, i)
            voice, rate = entry_voice_rate(a)
            spk = spoken_text(text)
            words = None
            try:
                # SB_M14SYNC_20261107: uu tien duong thu CO moc tu
                # (synth_timed trong edge_tts_raw ban moi); ban cu chua
                # co thi thu nhu cu va bang nay khong co bang moc.
                if hasattr(edge_tts_raw, 'synth_timed'):
                    n, words = edge_tts_raw.synth_timed(spk, voice, rate, out)
                else:
                    n = edge_tts_raw.synth(spk, voice, rate, out)
            except Exception as e:
                log('THU LOI %s bang %d: %s' % (did, i, e))
                continue
            if not n or not mp3_ok(out):
                log('MP3 KHONG HOP LE %s bang %d (bytes=%s)' % (did, i, n))
                continue
            # SB_M14SYNC_20261107: tinh bang moc khop tieng cho ban thu nay
            tm_map = None
            tw_list = None
            if words:
                tw_list = sync_word_stream(words) or None
                if tw_list and len(tw_list) > 800:
                    tw_list = tw_list[:800]
                bhtml = ''
                if i < len(boards) and isinstance(boards[i], dict):
                    bhtml = boards[i].get('html') or ''
                phrases = extract_sync_phrases(bhtml)
                if phrases:
                    tm_map = resolve_sync_tm(phrases, words)
            if r2('put', a['k'], out, 'audio/mpeg').returncode != 0:
                log('PUT R2 LOI %s bang %d' % (did, i))
                continue
            # SB_M14LIVEAUDIO_20261109: guong ngay vao bai dang chay neu
            # khop chu — doc lap voi viec lat ready o duoi (ban ghi co
            # the vua bi sua tiep hoac bi xoa sau xuat ban).
            try_mirror(uid, did, draft, 'board', i, a, out)
            # Đọc lại bản ghi tươi, chỉ lật ready khi hash còn khớp
            fresh = r2_get_json(base + '/' + did + '.json')
            fvoice, frate = entry_voice_rate(fresh['audio'][i]) \
                if isinstance(fresh, dict) and isinstance(fresh.get('audio'), list) \
                and i < len(fresh['audio']) else (None, None)
            if not isinstance(fresh, dict) or not isinstance(fresh.get('audio'), list) \
                    or i >= len(fresh['audio']) or not isinstance(fresh['audio'][i], dict) \
                    or fresh['audio'][i].get('h') != a.get('h') \
                    or fvoice != voice or frate != rate:
                log('BO QUA LAT READY %s bang %d (ban ghi da thay doi)' % (did, i))
                continue
            fresh['audio'][i]['s'] = 'ready'
            # SB_M14RECORD: kh = hash cua dung doan text file k vua thu tu do
            fresh['audio'][i]['kh'] = a.get('h')
            fresh['audio'][i]['v'] = voice
            fresh['audio'][i]['r'] = rate
            # SB_M14SYNC_20261107: bang moc khop tieng cua DUNG ban thu
            # nay (theo kh vua ghi); ban thu moi khong co moc thi xoa tm
            # cu de khong dung nham moc cua ban thu truoc.
            if tm_map:
                fresh['audio'][i]['tm'] = tm_map
            elif 'tm' in fresh['audio'][i]:
                del fresh['audio'][i]['tm']
            # SB_M14SYNC2_20261107: dong tu co moc cua ban thu (tw) —
            # phia web tu tra moc cho cum gan sau; ban thu moi khong co
            # moc tu thi xoa tw cu nhu tm.
            if tw_list:
                fresh['audio'][i]['tw'] = tw_list
            elif 'tw' in fresh['audio'][i]:
                del fresh['audio'][i]['tw']
            if r2_put_json(base + '/' + did + '.json', fresh):
                entry['au'] = audio_summary(fresh)
                index_dirty = True
                done += 1
                log('READY %s bang %d (%d bytes, %s %s)' % (did, i, n, voice, rate))
            try:
                os.remove(out)
            except Exception:
                pass
        # SB_M14QAUDIO_20261107: TIENG CUA CAU HOI (doc de bai) —
        # draft.qaudio song song voi danh sach cau PHANG theo thu tu bo
        # de; text thu = truong narr cua chinh cau. Cau khong co khung
        # khop tieng nen khong tinh tm/tw; chi lat ready + kh nhu bang.
        qau = draft.get('qaudio')
        if isinstance(qau, list) and qau:
            flatq = []
            for st in (draft.get('sets') or []):
                if isinstance(st, dict):
                    for qq in (st.get('questions') or []):
                        flatq.append(qq if isinstance(qq, dict) else {})
            for i, a in enumerate(qau):
                if not isinstance(a, dict) or a.get('s') != 'pending' or not a.get('k'):
                    continue
                text = ''
                if i < len(flatq):
                    text = flatq[i].get('narr') or ''
                if not str(text).strip():
                    continue
                out = '/tmp/mod14rec_%s_q%d.mp3' % (did, i)
                voice, rate = entry_voice_rate(a)
                spk = question_pause_text(spoken_text(text))  # SB_PAUSEFIX_20261109
                try:
                    if hasattr(edge_tts_raw, 'synth_timed'):
                        n, _w = edge_tts_raw.synth_timed(spk, voice, rate, out)
                    else:
                        n = edge_tts_raw.synth(spk, voice, rate, out)
                except Exception as e:
                    log('THU LOI %s cau %d: %s' % (did, i, e))
                    continue
                if not n or not mp3_ok(out):
                    log('MP3 KHONG HOP LE %s cau %d (bytes=%s)' % (did, i, n))
                    continue
                if r2('put', a['k'], out, 'audio/mpeg').returncode != 0:
                    log('PUT R2 LOI %s cau %d' % (did, i))
                    continue
                try_mirror(uid, did, draft, 'question', i, a, out)  # SB_M14LIVEAUDIO_20261109
                fresh = r2_get_json(base + '/' + did + '.json')
                fq = fresh.get('qaudio') if isinstance(fresh, dict) else None
                fvoice, frate = entry_voice_rate(fq[i]) \
                    if isinstance(fq, list) and i < len(fq) and isinstance(fq[i], dict) else (None, None)
                if not isinstance(fq, list) or i >= len(fq) or not isinstance(fq[i], dict) \
                        or fq[i].get('h') != a.get('h') or fvoice != voice or frate != rate:
                    log('BO QUA LAT READY %s cau %d (ban ghi da thay doi)' % (did, i))
                    continue
                fq[i]['s'] = 'ready'
                fq[i]['kh'] = a.get('h')
                fq[i]['v'] = voice
                fq[i]['r'] = rate
                if r2_put_json(base + '/' + did + '.json', fresh):
                    entry['au'] = audio_summary(fresh)
                    index_dirty = True
                    done += 1
                    log('READY %s cau %d (%d bytes, %s %s)' % (did, i, n, voice, rate))
                try:
                    os.remove(out)
                except Exception:
                    pass
        # SB_M14KTBC_20261109: TIENG CUA CAU KIEM TRA BAI CU —
        # draft.raudio song song voi draft.review.checks; text thu =
        # truong narr cua chinh cau KTBC; file dich nam trong chinh
        # entry k (500 + tong cau than bai + j) nen khong dung vung
        # file cua cau than bai; lat ready + kh nhu khoi qaudio.
        rau = draft.get('raudio')
        if isinstance(rau, list) and rau:
            rchecks = []
            rv = draft.get('review')
            if isinstance(rv, dict) and isinstance(rv.get('checks'), list):
                rchecks = [c if isinstance(c, dict) else {} for c in rv.get('checks')]
            for i, a in enumerate(rau):
                if not isinstance(a, dict) or a.get('s') != 'pending' or not a.get('k'):
                    continue
                text = ''
                if i < len(rchecks):
                    text = rchecks[i].get('narr') or ''
                if not str(text).strip():
                    continue
                out = '/tmp/mod14rec_%s_r%d.mp3' % (did, i)
                voice, rate = entry_voice_rate(a)
                spk = question_pause_text(spoken_text(text))  # SB_PAUSEFIX_20261109
                try:
                    if hasattr(edge_tts_raw, 'synth_timed'):
                        n, _w = edge_tts_raw.synth_timed(spk, voice, rate, out)
                    else:
                        n = edge_tts_raw.synth(spk, voice, rate, out)
                except Exception as e:
                    log('THU LOI %s cau KTBC %d: %s' % (did, i, e))
                    continue
                if not n or not mp3_ok(out):
                    log('MP3 KHONG HOP LE %s cau KTBC %d (bytes=%s)' % (did, i, n))
                    continue
                if r2('put', a['k'], out, 'audio/mpeg').returncode != 0:
                    log('PUT R2 LOI %s cau KTBC %d' % (did, i))
                    continue
                fresh = r2_get_json(base + '/' + did + '.json')
                fq = fresh.get('raudio') if isinstance(fresh, dict) else None
                fvoice, frate = entry_voice_rate(fq[i]) \
                    if isinstance(fq, list) and i < len(fq) and isinstance(fq[i], dict) else (None, None)
                if not isinstance(fq, list) or i >= len(fq) or not isinstance(fq[i], dict) \
                        or fq[i].get('h') != a.get('h') or fvoice != voice or frate != rate:
                    log('BO QUA LAT READY %s cau KTBC %d (ban ghi da thay doi)' % (did, i))
                    continue
                fq[i]['s'] = 'ready'
                fq[i]['kh'] = a.get('h')
                fq[i]['v'] = voice
                fq[i]['r'] = rate
                if r2_put_json(base + '/' + did + '.json', fresh):
                    entry['au'] = audio_summary(fresh)
                    index_dirty = True
                    done += 1
                    log('READY %s cau KTBC %d (%d bytes, %s %s)' % (did, i, n, voice, rate))
                    if draft.get('packJob'):  # SB_M14KTBC_AU_20261110: gan tham chieu vao goi dich
                        job_set_review_field(uid, did, draft, i, 'audio',
                                             os.path.basename(a['k']), _guard_same_question)
                try:
                    os.remove(out)
                except Exception:
                    pass
        # SB_M14KTBC_AU_20261110: TIENG GIAI THICH CUA CAU KTBC —
        # draft.reaudio song song review.checks; text = truong explain
        # cua chinh cau (doc thuong Mau 3, khong ngat phuong an).
        reau = draft.get('reaudio')
        if isinstance(reau, list) and reau:
            rchecks2 = []
            rv2 = draft.get('review')
            if isinstance(rv2, dict) and isinstance(rv2.get('checks'), list):
                rchecks2 = [c if isinstance(c, dict) else {} for c in rv2.get('checks')]
            for i, a in enumerate(reau):
                if not isinstance(a, dict) or a.get('s') != 'pending' or not a.get('k'):
                    continue
                text = ''
                if i < len(rchecks2):
                    text = rchecks2[i].get('explain') or ''
                if not str(text).strip():
                    continue
                out = '/tmp/mod14rec_%s_e%d.mp3' % (did, i)
                voice, rate = entry_voice_rate(a)
                spk = spoken_text(text)
                try:
                    n = edge_tts_raw.synth(spk, voice, rate, out)
                except Exception as e:
                    log('THU LOI %s giai thich KTBC %d: %s' % (did, i, e))
                    continue
                if not n or not mp3_ok(out):
                    log('MP3 KHONG HOP LE %s giai thich KTBC %d (bytes=%s)' % (did, i, n))
                    continue
                if r2('put', a['k'], out, 'audio/mpeg').returncode != 0:
                    log('PUT R2 LOI %s giai thich KTBC %d' % (did, i))
                    continue
                fresh = r2_get_json(base + '/' + did + '.json')
                fq = fresh.get('reaudio') if isinstance(fresh, dict) else None
                fvoice, frate = entry_voice_rate(fq[i]) \
                    if isinstance(fq, list) and i < len(fq) and isinstance(fq[i], dict) else (None, None)
                if not isinstance(fq, list) or i >= len(fq) or not isinstance(fq[i], dict) \
                        or fq[i].get('h') != a.get('h') or fvoice != voice or frate != rate:
                    log('BO QUA LAT READY %s giai thich KTBC %d (ban ghi da thay doi)' % (did, i))
                    continue
                fq[i]['s'] = 'ready'
                fq[i]['kh'] = a.get('h')
                fq[i]['v'] = voice
                fq[i]['r'] = rate
                if r2_put_json(base + '/' + did + '.json', fresh):
                    entry['au'] = audio_summary(fresh)
                    index_dirty = True
                    done += 1
                    log('READY %s giai thich KTBC %d (%d bytes)' % (did, i, n))
                    if draft.get('packJob'):
                        job_set_review_field(uid, did, draft, i, 'audioE',
                                             os.path.basename(a['k']), _guard_same_text('explain'))
                try:
                    os.remove(out)
                except Exception:
                    pass
        # SB_M14KTBC_AU_20261110: TIENG NHANH HS CHON SAI CUA CAU KTBC —
        # draft.rwaudio[j] la mang 4 muc theo phuong an; text = truong
        # say cua wrong[i] trong chinh cau (giong co nhu giai thich).
        rwau = draft.get('rwaudio')
        if isinstance(rwau, list) and rwau:
            rchecks3 = []
            rv3 = draft.get('review')
            if isinstance(rv3, dict) and isinstance(rv3.get('checks'), list):
                rchecks3 = [c if isinstance(c, dict) else {} for c in rv3.get('checks')]
            for j, row in enumerate(rwau):
                if not isinstance(row, list):
                    continue
                for wi, a in enumerate(row):
                    if not isinstance(a, dict) or a.get('s') != 'pending' or not a.get('k'):
                        continue
                    text = ''
                    if j < len(rchecks3):
                        w = rchecks3[j].get('wrong')
                        if isinstance(w, list) and wi < len(w) and isinstance(w[wi], dict):
                            text = w[wi].get('say') or ''
                    if not str(text).strip():
                        continue
                    out = '/tmp/mod14rec_%s_w%d_%d.mp3' % (did, j, wi)
                    voice, rate = entry_voice_rate(a)
                    spk = spoken_text(text)
                    try:
                        n = edge_tts_raw.synth(spk, voice, rate, out)
                    except Exception as e:
                        log('THU LOI %s nhanh sai KTBC %d/%d: %s' % (did, j, wi, e))
                        continue
                    if not n or not mp3_ok(out):
                        log('MP3 KHONG HOP LE %s nhanh sai KTBC %d/%d (bytes=%s)' % (did, j, wi, n))
                        continue
                    if r2('put', a['k'], out, 'audio/mpeg').returncode != 0:
                        log('PUT R2 LOI %s nhanh sai KTBC %d/%d' % (did, j, wi))
                        continue
                    fresh = r2_get_json(base + '/' + did + '.json')
                    fr = fresh.get('rwaudio') if isinstance(fresh, dict) else None
                    frow = fr[j] if isinstance(fr, list) and j < len(fr) else None
                    fe = frow[wi] if isinstance(frow, list) and wi < len(frow) else None
                    fvoice, frate = entry_voice_rate(fe) if isinstance(fe, dict) else (None, None)
                    if not isinstance(fe, dict) or fe.get('h') != a.get('h') \
                            or fvoice != voice or frate != rate:
                        log('BO QUA LAT READY %s nhanh sai KTBC %d/%d (ban ghi da thay doi)' % (did, j, wi))
                        continue
                    fe['s'] = 'ready'
                    fe['kh'] = a.get('h')
                    fe['v'] = voice
                    fe['r'] = rate
                    if r2_put_json(base + '/' + did + '.json', fresh):
                        entry['au'] = audio_summary(fresh)
                        index_dirty = True
                        done += 1
                        log('READY %s nhanh sai KTBC %d/%d (%d bytes)' % (did, j, wi, n))
                        if draft.get('packJob'):
                            job_set_review_field(uid, did, draft, j, 'wrong',
                                                 (wi, os.path.basename(a['k'])), None)
                    try:
                        os.remove(out)
                    except Exception:
                        pass
        # SB_M14KTBC_AU_20261110: TIENG HOC SINH CUA CAU KTBC —
        # draft.rkaudio[j] = {h, s, voices: [{k, s} x4]}; cau HS noi
        # dung tu kid_line(check); thu tung giong con thieu bang
        # pipeline 4 giong da duyet; du 4 giong moi lat ready + kh.
        rkau = draft.get('rkaudio')
        if isinstance(rkau, list) and rkau:
            rchecks4 = []
            rv4 = draft.get('review')
            if isinstance(rv4, dict) and isinstance(rv4.get('checks'), list):
                rchecks4 = [c if isinstance(c, dict) else {} for c in rv4.get('checks')]
            for j, a in enumerate(rkau):
                if not isinstance(a, dict) or a.get('s') == 'ready':
                    continue
                voices = a.get('voices')
                if not isinstance(voices, list) or len(voices) != 4:
                    continue
                if j >= len(rchecks4):
                    continue
                line = kid_line(rchecks4[j])
                if not line:
                    continue
                if texthash(kid_hash_source(rchecks4[j])) != a.get('h'):
                    continue  # hash nguon khong khop muc — ban ghi da doi, cho trang xep lai
                changed = False
                for vi, ve in enumerate(voices):
                    if not isinstance(ve, dict) or ve.get('s') == 'ready' or not ve.get('k'):
                        continue
                    out = '/tmp/mod14rec_%s_k%d_%d.mp3' % (did, j, vi)
                    try:
                        n = synth_kid(line, vi, out)
                    except Exception as e:
                        log('THU LOI %s tieng HS KTBC %d giong %d: %s' % (did, j, vi, e))
                        continue
                    if not n:
                        log('MP3 KHONG HOP LE %s tieng HS KTBC %d giong %d' % (did, j, vi))
                        continue
                    if r2('put', ve['k'], out, 'audio/mpeg').returncode != 0:
                        log('PUT R2 LOI %s tieng HS KTBC %d giong %d' % (did, j, vi))
                        continue
                    ve['s'] = 'ready'
                    changed = True
                    log('READY %s tieng HS KTBC %d giong %s (%d bytes)' % (did, j, ['a', 'a2', 'a3', 'a4'][vi], n))
                    try:
                        os.remove(out)
                    except Exception:
                        pass
                if not changed:
                    continue
                fresh = r2_get_json(base + '/' + did + '.json')
                fk = fresh.get('rkaudio') if isinstance(fresh, dict) else None
                fe = fk[j] if isinstance(fk, list) and j < len(fk) else None
                if not isinstance(fe, dict) or fe.get('h') != a.get('h') \
                        or not isinstance(fe.get('voices'), list) or len(fe['voices']) != 4:
                    log('BO QUA LAT READY %s tieng HS KTBC %d (ban ghi da thay doi)' % (did, j))
                    continue
                for vi in range(4):
                    if isinstance(voices[vi], dict) and voices[vi].get('s') == 'ready' \
                            and isinstance(fe['voices'][vi], dict):
                        fe['voices'][vi]['s'] = 'ready'
                if all(isinstance(v, dict) and v.get('s') == 'ready' for v in fe['voices']):
                    fe['s'] = 'ready'
                    fe['kh'] = a.get('h')
                if r2_put_json(base + '/' + did + '.json', fresh):
                    entry['au'] = audio_summary(fresh)
                    index_dirty = True
                    done += 1
                    if fe.get('s') == 'ready' and draft.get('packJob'):
                        stem = os.path.basename(voices[0]['k'])
                        if stem.endswith('a.mp3'):
                            job_set_review_field(uid, did, draft, j, 'audioA', stem, _guard_same_kid)
        # SB_M14KTBC_AU_20261110: CLIP MO DAU KTBC (rintro) cua ban ghi
        # LENH sua-goi — draft.iraudio la 1 muc don, text thu nam o
        # draft.introSay (loi dan bang mo dau); nhap thuong khong dung
        # duong nay (bang mo dau thu qua draft.audio nhu bang thuong).
        irau = draft.get('iraudio')
        if isinstance(irau, dict) and irau.get('s') == 'pending' and irau.get('k'):
            text = draft.get('introSay') or ''
            if str(text).strip():
                out = '/tmp/mod14rec_%s_intro.mp3' % did
                voice, rate = entry_voice_rate(irau)
                spk = spoken_text(text)
                try:
                    n = edge_tts_raw.synth(spk, voice, rate, out)
                except Exception as e:
                    log('THU LOI %s mo dau KTBC: %s' % (did, e))
                    n = 0
                if n and mp3_ok(out):
                    if r2('put', irau['k'], out, 'audio/mpeg').returncode != 0:
                        log('PUT R2 LOI %s mo dau KTBC' % did)
                    else:
                        fresh = r2_get_json(base + '/' + did + '.json')
                        fi = fresh.get('iraudio') if isinstance(fresh, dict) else None
                        fvoice, frate = entry_voice_rate(fi) if isinstance(fi, dict) else (None, None)
                        if not isinstance(fi, dict) or fi.get('h') != irau.get('h') \
                                or fvoice != voice or frate != rate:
                            log('BO QUA LAT READY %s mo dau KTBC (ban ghi da thay doi)' % did)
                        else:
                            fi['s'] = 'ready'
                            fi['kh'] = irau.get('h')
                            fi['v'] = voice
                            fi['r'] = rate
                            if r2_put_json(base + '/' + did + '.json', fresh):
                                entry['au'] = audio_summary(fresh)
                                index_dirty = True
                                done += 1
                                log('READY %s mo dau KTBC (%d bytes)' % (did, n))
                                if draft.get('packJob'):
                                    job_set_intro(uid, did, draft, str(text),
                                                  os.path.basename(irau['k']))
                        try:
                            os.remove(out)
                        except Exception:
                            pass
        # SB_M14LEVELS_20261107: TIENG CUA MUC "EM CHUA HIEU" (L1..L4
        # theo tung bang) — draft.laudio PHANG theo vi tri
        # bang-trong-goi * 4 + (muc - 1); text thu = truong say cua
        # muc trong draft.boards[bi].levels[lv]; muc khong co khung
        # khop tieng nen khong tinh tm/tw; chi lat ready + kh nhu cau.
        lau = draft.get('laudio')
        if isinstance(lau, list) and lau:
            for slot, a in enumerate(lau):
                if not isinstance(a, dict) or a.get('s') != 'pending' or not a.get('k'):
                    continue
                bi, lv = divmod(slot, 4)
                text = ''
                if bi < len(boards) and isinstance(boards[bi], dict):
                    lvs = boards[bi].get('levels')
                    if isinstance(lvs, list) and lv < len(lvs) and isinstance(lvs[lv], dict):
                        text = lvs[lv].get('say') or ''
                if not str(text).strip():
                    continue
                out = '/tmp/mod14rec_%s_L%d.mp3' % (did, slot)
                voice, rate = entry_voice_rate(a)
                spk = spoken_text(text)
                try:
                    if hasattr(edge_tts_raw, 'synth_timed'):
                        n, _w = edge_tts_raw.synth_timed(spk, voice, rate, out)
                    else:
                        n = edge_tts_raw.synth(spk, voice, rate, out)
                except Exception as e:
                    log('THU LOI %s muc %d: %s' % (did, slot, e))
                    continue
                if not n or not mp3_ok(out):
                    log('MP3 KHONG HOP LE %s muc %d (bytes=%s)' % (did, slot, n))
                    continue
                if r2('put', a['k'], out, 'audio/mpeg').returncode != 0:
                    log('PUT R2 LOI %s muc %d' % (did, slot))
                    continue
                try_mirror(uid, did, draft, 'level', slot, a, out)  # SB_M14LIVEAUDIO_20261109
                fresh = r2_get_json(base + '/' + did + '.json')
                fl = fresh.get('laudio') if isinstance(fresh, dict) else None
                fvoice, frate = entry_voice_rate(fl[slot]) \
                    if isinstance(fl, list) and slot < len(fl) and isinstance(fl[slot], dict) else (None, None)
                if not isinstance(fl, list) or slot >= len(fl) or not isinstance(fl[slot], dict) \
                        or fl[slot].get('h') != a.get('h') or fvoice != voice or frate != rate:
                    log('BO QUA LAT READY %s muc %d (ban ghi da thay doi)' % (did, slot))
                    continue
                fl[slot]['s'] = 'ready'
                fl[slot]['kh'] = a.get('h')
                fl[slot]['v'] = voice
                fl[slot]['r'] = rate
                if r2_put_json(base + '/' + did + '.json', fresh):
                    entry['au'] = audio_summary(fresh)
                    index_dirty = True
                    done += 1
                    log('READY %s muc %d (%d bytes, %s %s)' % (did, slot, n, voice, rate))
                try:
                    os.remove(out)
                except Exception:
                    pass
    if index_dirty:
        r2_put_json(base + '/index.json', index)
    return done


def main():
    # SB_GHRECORDER_20261110: khong con khoa file /tmp — GitHub Actions
    # khoa bang nhom concurrency; chay song song voi ban cuc bo thi cong
    # hash khi lat ready tren hang cho R2 chan ghi de trang thai.
    only = [x.strip() for x in (os.environ.get('MOD14_ONLY_UID') or '').split(',') if x.strip()]
    total = 0
    errs = []
    keys = r2_list('data/mod14-drafts/')
    uids = []
    for k in keys:
        parts = k.split('/')
        if len(parts) == 4 and parts[3] == 'index.json' and parts[2] not in uids:
            uids.append(parts[2])
    if only:
        uids = [u for u in uids if u in only]
    for uid in uids:
        try:
            total += process_uid(uid)
        except Exception as e:
            log('LOI uid %s: %s' % (uid, e))
            errs.append('%s: %s' % (uid, str(e)[:200]))
    if total:
        log('XONG luot: %d bang da thu' % total)
    # Tom tat luot chay -> R2 de kiem tra tu ben ngoai (khong co quyen
    # xem trang Actions cung thay duoc trang thai bo thu).
    try:
        summary = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                   'runner': 'github-actions', 'done': total,
                   'uids': uids, 'errors': errs[:20]}
        _s3c().put_object(Bucket=BUCKET, Key='data/gh-audio-worker/last-run.json',
                          Body=json.dumps(summary, ensure_ascii=False).encode('utf-8'),
                          ContentType='application/json; charset=utf-8')
    except Exception:
        pass
    # gọt log giữ 300 dòng cuối
    try:
        lines = open(LOGF, encoding='utf-8').read().splitlines()
        if len(lines) > 300:
            open(LOGF, 'w', encoding='utf-8').write('\n'.join(lines[-300:]) + '\n')
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
