# Minimal raw WebSocket client for Edge TTS through the egress proxy (CONNECT tunnel).
# Needed because aiohttp/websockets choke on the proxied 101 response headers.
# SB_GHRECORDER_20261110: tren GitHub Actions khong co proxy — open_tunnel()
# tu noi thang TLS toi may chu Edge khi bien moi truong proxy trong.
import asyncio, os, ssl, sys, struct, secrets
import socket
from urllib.parse import urlparse
from edge_tts.constants import WSS_URL, SEC_MS_GEC_VERSION
from edge_tts.drm import DRM
from edge_tts.communicate import ssml_headers_plus_data, get_headers_and_data, date_to_string, connect_id, mkssml
from edge_tts.data_classes import TTSConfig
from xml.sax.saxutils import escape as _xml_escape  # SB_ARRAYWORDS_20261109: mkssml() khong tu thoat ky tu XML — & < > tho lam vo SSML, Edge dong ws

PROXY = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
HOST = 'speech.platform.bing.com'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0')

def open_tunnel():
    if not PROXY:
        # SB_GHRECORDER_20261110: noi thang (GitHub Actions khong dung proxy).
        s = socket.create_connection((HOST, 443), timeout=30)
        ctx = ssl._create_unverified_context()
        return ctx.wrap_socket(s, server_hostname=HOST)
    pu = urlparse(PROXY)
    s = socket.create_connection((pu.hostname, pu.port), timeout=30)
    auth = ''
    if pu.username:
        import base64
        cred = base64.b64encode(f"{pu.username}:{pu.password}".encode()).decode()
        auth = f"Proxy-Authorization: Basic {cred}\r\n"
    req = (f"CONNECT {HOST}:443 HTTP/1.1\r\nHost: {HOST}:443\r\n{auth}Proxy-Connection: Keep-Alive\r\n\r\n")
    s.sendall(req.encode())
    buf = b''
    while b'\r\n\r\n' not in buf:
        chunk = s.recv(4096)
        if not chunk:
            raise RuntimeError('proxy CONNECT closed')
        buf += chunk
    head = buf.split(b'\r\n\r\n')[0].decode('latin1')
    if ' 200' not in head.split('\r\n')[0]:
        raise RuntimeError('CONNECT failed: ' + head.split('\r\n')[0])
    ctx = ssl._create_unverified_context()
    return ctx.wrap_socket(s, server_hostname=HOST)

class WS:
    def __init__(self, sock):
        self.s = sock
        self.buf = bytearray()
    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise RuntimeError('socket closed')
            self.buf.extend(chunk)
        out = bytes(self.buf[:n]); del self.buf[:n]
        return out
    def handshake(self, path):
        import base64
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        muid = DRM.generate_muid()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {HOST}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Origin: chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold\r\n"
            f"User-Agent: {UA}\r\n"
            "Pragma: no-cache\r\nCache-Control: no-cache\r\n"
            "Accept-Language: en-US,en;q=0.9\r\n"
            f"Cookie: muid={muid};\r\n"
            "\r\n"
        )
        self.s.sendall(req.encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = self.s.recv(4096)
            if not chunk:
                raise RuntimeError('handshake closed')
            buf += chunk
        head, rest = buf.split(b'\r\n\r\n', 1)
        status = head.split(b'\r\n')[0].decode('latin1')
        if ' 101' not in status:
            raise RuntimeError('handshake rejected: ' + status + ' || ' + head.decode('latin1')[:300])
        self.buf.extend(rest)
        return status
    def send_text(self, text):
        data = text.encode('utf-8')
        mask = secrets.token_bytes(4)
        n = len(data)
        if n < 126:
            hdr = struct.pack('BB', 0x81, 0x80 | n)
        elif n < 65536:
            hdr = struct.pack('>BBH', 0x81, 0x80 | 126, n)
        else:
            hdr = struct.pack('>BBQ', 0x81, 0x80 | 127, n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.s.sendall(hdr + mask + masked)
    def send_pong(self, payload):
        mask = secrets.token_bytes(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.s.sendall(struct.pack('BB', 0x8A, 0x80 | len(payload)) + mask + masked)
    def recv_msg(self):
        # returns (opcode, payload) for a complete (possibly fragmented) message
        payload = bytearray(); op_start = None
        while True:
            b1, b2 = self._read(2)
            fin = b1 & 0x80; op = b1 & 0x0F
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack('>H', self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack('>Q', self._read(8))[0]
            data = self._read(ln) if ln else b''
            if op == 9:  # ping
                self.send_pong(data); continue
            if op == 8:
                raise RuntimeError('server closed ws')
            if op_start is None:
                op_start = op
            payload.extend(data)
            if fin:
                return op_start, bytes(payload)

# ===== SB_PAUSEFIX_20261109: QUY UOC NGAT THAT TRONG LOI DOC =====
# Van de goc: Edge doc "…tan b. B. Sin…" LIEN MOT HOI — chu cai phuong
# an dung mot minh + dau cham bi Edge hieu la viet tat/ten rieng viet
# tat nen KHONG coi la het cau, ngat giua cac phuong an = 0ms (da do
# bang WordBoundary tren clip that cua goi Toan 11). Dau cham thong
# thuong khong sua duoc: phai cat doan + chen IM LANG THAT.
# Quy uoc: lop ghep loi (recorder cho cau hoi, trang lop cho giong may)
# chen danh dau PAUSE_MARK (ngat dai — giua cac phuong an) /
# PAUSE_MARK_S (ngat ngan — giua cau dan va phuong an A) vao text.
# synth/synth_timed gap danh dau thi cat text tai danh dau, thu tung
# doan, CAT im lang thua hai dau moi doan (Edge tu chen ~1,1s ngat cau
# vao duoi doan neu doan ket bang dau cham — giu nguyen cong them se
# qua dai) roi noi lai bang blob im lang MP3 dung dinh dang Edge
# (24kHz mono 48kbit) sao cho TONG quang lang giua hai tu do = PAUSE_SEC
# / PAUSE_S_SEC (0,70s / 0,45s, da gom le giu lai hai phia).
# Text khong co danh dau -> duong cu NGUYEN VEN (moi duong thu khac
# khong bi anh huong).
PAUSE_MARK = '@@PAUSE@@'
PAUSE_MARK_S = '@@PAUSE_S@@'
PAUSE_SEC = 0.70
PAUSE_S_SEC = 0.45
_PAUSE_TAIL_KEEP = 0.12   # giay im lang giu lai o duoi doan truoc
_PAUSE_HEAD_KEEP = 0.06   # giay im lang giu lai o dau doan sau
_PAUSE_CACHE = {}

def _split_pauses(text):
    """Cat text tai cac danh dau ngat -> [(doan, giay ngat SAU doan)]."""
    import re as _re
    parts = _re.split('(' + _re.escape(PAUSE_MARK_S) + '|' + _re.escape(PAUSE_MARK) + ')', text)
    segs = []
    for i in range(0, len(parts), 2):
        seg = (parts[i] or '').strip()
        mark = parts[i + 1] if i + 1 < len(parts) else ''
        pause = PAUSE_S_SEC if mark == PAUSE_MARK_S else (PAUSE_SEC if mark == PAUSE_MARK else 0.0)
        if seg:
            segs.append([seg, pause])
        elif pause and segs:
            segs[-1][1] = pause
    return [(s, p) for s, p in segs]

def _silence_blob(sec):
    """(bytes MP3 im lang, ms do that) tao bang ffmpeg dung dinh dang
    cua Edge; cache theo so giay. Loi ffmpeg -> (b'', 0) de duong thu
    chi mat ngat chu khong chet."""
    key = round(float(sec), 3)
    if key in _PAUSE_CACHE:
        return _PAUSE_CACHE[key]
    blob, ms = b'', 0.0
    try:
        import subprocess, tempfile
        fd, tmp = tempfile.mkstemp(suffix='.mp3'); os.close(fd)
        r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi',
                            '-i', 'anullsrc=r=24000:cl=mono', '-t', str(key),
                            '-codec:a', 'libmp3lame', '-b:a', '48k', tmp],
                           capture_output=True, timeout=30)
        if r.returncode == 0 and os.path.exists(tmp):
            blob = open(tmp, 'rb').read()
            ms = _mp3_duration_ms(tmp)
        try: os.remove(tmp)
        except Exception: pass
    except Exception:
        blob, ms = b'', 0.0
    _PAUSE_CACHE[key] = (blob, ms)
    return blob, ms

def _seg_speech_bounds(path):
    """(moc bat dau tieng, moc het tieng, tong dai) cua 1 file MP3 do
    bang silencedetect -50dB; loi do -> (0, dur, dur)."""
    dur = _mp3_duration_ms(path) / 1000.0
    try:
        import subprocess, re as _re
        r = subprocess.run(['ffmpeg', '-i', path, '-af', 'silencedetect=noise=-50dB:d=0.05',
                            '-f', 'null', '-'], capture_output=True, text=True, timeout=60)
        ev = _re.findall(r'silence_(start|end): ([0-9.]+)', r.stderr or '')
        start, end = 0.0, dur
        if ev and ev[0][0] == 'start' and float(ev[0][1]) < 0.01 and len(ev) >= 2:
            start = float(ev[1][1])
        if ev and ev[-1][0] == 'end' and abs(float(ev[-1][1]) - dur) < 0.05 and len(ev) >= 2:
            end = float(ev[-2][1])
        if end <= start:
            return 0.0, dur, dur
        return start, end, dur
    except Exception:
        return 0.0, dur, dur

def _trim_mp3(src, dst, start, length):
    import subprocess
    r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', '%.3f' % start,
                        '-t', '%.3f' % length, '-i', src,
                        '-codec:a', 'libmp3lame', '-b:a', '48k', dst],
                       capture_output=True, timeout=60)
    return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0

def _paused_render(text, voice, rate, out, timed):
    """Duong thu cho text CO danh dau ngat (dung chung synth/synth_timed):
    thu tung doan, cat im lang thua hai dau, noi lai voi quang lang
    tong dung PAUSE_SEC/PAUSE_S_SEC. Tra (so byte, words|None)."""
    segs = _split_pauses(text)
    raws, seg_words = [], []
    for i, (seg, _p) in enumerate(segs):
        tmp = out + '.praw%d.mp3' % i
        if timed:
            _n, ws = synth_timed(seg, voice, rate, tmp)
        else:
            synth(seg, voice, rate, tmp); ws = None
        raws.append(tmp); seg_words.append(ws)
    blobs, words, offset = [], [], 0.0
    for i, (seg, pause) in enumerate(segs):
        s0, s1, dur = _seg_speech_bounds(raws[i])
        cut0 = 0.0 if i == 0 else max(0.0, s0 - _PAUSE_HEAD_KEEP)
        cut1 = dur if i == len(segs) - 1 else min(dur, s1 + _PAUSE_TAIL_KEEP)
        use = raws[i]
        if cut1 - cut0 < dur - 0.01:
            tmp2 = out + '.ptrim%d.mp3' % i
            if _trim_mp3(raws[i], tmp2, cut0, cut1 - cut0):
                use = tmp2
            else:
                cut0 = 0.0
        tdur = _mp3_duration_ms(use)
        if not tdur and seg_words[i]:
            tdur = max(w['o'] + w['d'] for w in seg_words[i]) - cut0 * 1000.0
        if seg_words[i]:
            for w in seg_words[i]:
                words.append({'o': int(round(w['o'] - cut0 * 1000.0 + offset)), 'd': w['d'], 'w': w['w']})
        blobs.append(open(use, 'rb').read())
        offset += tdur
        if use != raws[i]:
            try: os.remove(use)
            except Exception: pass
        try: os.remove(raws[i])
        except Exception: pass
        if pause:
            ins = max(0.0, pause - _PAUSE_TAIL_KEEP - _PAUSE_HEAD_KEEP)
            blob, ms = _silence_blob(ins)
            if blob:
                blobs.append(blob); offset += ms
    with open(out, 'wb') as f:
        for b in blobs: f.write(b)
    return sum(len(b) for b in blobs), (words if timed else None)

def split_text(text, maxlen=900):
    import re as _re
    sents = _re.split(r'(?<=[.!?…])\s+', text.strip())
    parts, cur = [], ''
    for s in sents:
        while len(s) > maxlen:
            cut = s.rfind(' ', 0, maxlen)
            if cut < 200: cut = maxlen
            parts.append(s[:cut]); s = s[cut:].lstrip()
        if len(cur) + len(s) + 1 <= maxlen: cur = (cur + ' ' + s).strip()
        else: parts.append(cur); cur = s
    if cur: parts.append(cur)
    return [p for p in parts if p]

def synth(text, voice, rate, out):
    if PAUSE_MARK in text or PAUSE_MARK_S in text:  # SB_PAUSEFIX_20261109
        n, _w = _paused_render(text, voice, rate, out, False)
        return n
    parts = split_text(text)
    if len(parts) > 1:
        import tempfile
        blobs = []
        for i, p in enumerate(parts):
            tmp = out + '.part%d.mp3' % i
            synth(p, voice, rate, tmp)
            blobs.append(open(tmp, 'rb').read()); os.remove(tmp)
        with open(out, 'wb') as f:
            for b in blobs: f.write(b)
        return sum(len(b) for b in blobs)
    cfg = TTSConfig(voice, rate, '+0%', '+0Hz', 'SentenceBoundary')
    path = (f"/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken=6A5AA1D4EAFF4E9FB37E23D68491D6F4"
            f"&ConnectionId={connect_id()}&Sec-MS-GEC={DRM.generate_sec_ms_gec()}&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}")
    sock = open_tunnel()
    ws = WS(sock)
    ws.handshake(path)
    ws.send_text(
        f"X-Timestamp:{date_to_string()}\r\n"
        "Content-Type:application/json; charset=utf-8\r\n"
        "Path:speech.config\r\n\r\n"
        '{"context":{"synthesis":{"audio":{"metadataoptions":{'
        '"sentenceBoundaryEnabled":"true","wordBoundaryEnabled":"false"'
        "},"
        '"outputFormat":"audio-24khz-48kbitrate-mono-mp3"'
        "}}}}\r\n"
    )
    ws.send_text(ssml_headers_plus_data(connect_id(), date_to_string(), mkssml(cfg, _xml_escape(text))))  # SB_ARRAYWORDS_20261109: thoat XML (& < >) — mkssml KHONG tu thoat, & tho lam vo SSML -> Edge dong ws
    audio = bytearray()
    sock.settimeout(60)
    while True:
        op, payload = ws.recv_msg()
        if op == 1:
            if b'Path:turn.end' in payload:
                break
        elif op == 2:
            hl = int.from_bytes(payload[:2], 'big')
            blob = payload[2:2+hl]
            if b'Path:audio' in blob:
                audio.extend(payload[2+hl:])
    sock.close()
    if not audio:
        raise RuntimeError('no audio received')
    with open(out, 'wb') as f:
        f.write(bytes(audio))
    return len(audio)

def _mp3_duration_ms(path):
    try:
        import subprocess
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'csv=p=0', path], capture_output=True, text=True, timeout=30)
        return float((r.stdout or '0').strip() or 0) * 1000.0
    except Exception:
        return 0.0
def synth_timed(text, voice, rate, out):
    """SB_M14SYNC_20261107: nhu synth() nhung BAT wordBoundaryEnabled va
    tra them moc tung tu: (so byte MP3, [{'o': ms bat dau, 'd': ms dai,
    'w': tu}...]) do tu su kien WordBoundary cua Edge TTS (Offset/Duration
    goc la don vi 100ns). Text dai bi cat nhieu doan (split_text) thi moc
    cua doan sau duoc cong don theo THOI LUONG DO THAT cua file doan
    truoc (ffprobe; khong co ffprobe thi lay moc cuoi cua doan truoc)."""
    if PAUSE_MARK in text or PAUSE_MARK_S in text:  # SB_PAUSEFIX_20261109: moc tu da cong ca quang ngat
        return _paused_render(text, voice, rate, out, True)
    parts = split_text(text)
    if len(parts) > 1:
        blobs = []; words = []; offset = 0.0
        for i, p in enumerate(parts):
            tmp = out + '.part%d.mp3' % i
            _, ws = synth_timed(p, voice, rate, tmp)
            dur = _mp3_duration_ms(tmp)
            if not dur and ws:
                dur = max(w['o'] + w['d'] for w in ws)
            for w in ws:
                words.append({'o': int(round(w['o'] + offset)), 'd': w['d'], 'w': w['w']})
            blobs.append(open(tmp, 'rb').read()); os.remove(tmp)
            offset += dur
        with open(out, 'wb') as f:
            for b in blobs: f.write(b)
        return sum(len(b) for b in blobs), words
    cfg = TTSConfig(voice, rate, '+0%', '+0Hz', 'WordBoundary')
    path = (f"/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken=6A5AA1D4EAFF4E9FB37E23D68491D6F4"
            f"&ConnectionId={connect_id()}&Sec-MS-GEC={DRM.generate_sec_ms_gec()}&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}")
    sock = open_tunnel()
    ws = WS(sock)
    ws.handshake(path)
    ws.send_text(
        f"X-Timestamp:{date_to_string()}\r\n"
        "Content-Type:application/json; charset=utf-8\r\n"
        "Path:speech.config\r\n\r\n"
        '{"context":{"synthesis":{"audio":{"metadataoptions":{'
        '"sentenceBoundaryEnabled":"true","wordBoundaryEnabled":"true"'
        "},"
        '"outputFormat":"audio-24khz-48kbitrate-mono-mp3"'
        "}}}}\r\n"
    )
    ws.send_text(ssml_headers_plus_data(connect_id(), date_to_string(), mkssml(cfg, _xml_escape(text))))  # SB_ARRAYWORDS_20261109: thoat XML (& < >) — mkssml KHONG tu thoat, & tho lam vo SSML -> Edge dong ws
    audio = bytearray(); words = []
    sock.settimeout(60)
    while True:
        op, payload = ws.recv_msg()
        if op == 1:
            if b'Path:turn.end' in payload:
                break
            if b'Path:audio.metadata' in payload:
                body = payload.split(b'\r\n\r\n', 1)[1] if b'\r\n\r\n' in payload else b''
                try:
                    import json as _json
                    meta = _json.loads(body.decode('utf-8'))
                    for it in meta.get('Metadata', []):
                        if it.get('Type') == 'WordBoundary':
                            d = it.get('Data') or {}
                            words.append({'o': int(round((d.get('Offset') or 0) / 10000.0)),
                                          'd': int(round((d.get('Duration') or 0) / 10000.0)),
                                          'w': ((d.get('text') or {}).get('Text') or '')})
                except Exception:
                    pass
        elif op == 2:
            hl = int.from_bytes(payload[:2], 'big')
            blob = payload[2:2+hl]
            if b'Path:audio' in blob:
                audio.extend(payload[2+hl:])
    sock.close()
    if not audio:
        raise RuntimeError('no audio received')
    with open(out, 'wb') as f:
        f.write(bytes(audio))
    return len(audio), words

if __name__ == '__main__':
    text, voice, rate, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    print('AUDIO BYTES', synth(text, voice, rate, out))
