import json, os, re, sys, time, subprocess, requests, shutil
def manifest(pack):
    man = {}
    w = 'em' if pack.get('grade', 3) >= 6 else 'con'
    W = 'Em' if w == 'em' else 'Con'
    man['intro'] = 'Chào ' + w + '! Cô là Cô Sao. Hôm nay cô trò mình cùng học bài: ' + pack['title'] + '. Bấm Nghe cô giảng để bắt đầu nào!'
    man['diag'] = 'Không sao đâu ' + w + ', ai học cũng có chỗ chưa hiểu. ' + W + ' bấm vào câu giống ý ' + w + ' nhất, cô sẽ giảng lại bằng cách khác.'
    man['retry'] = 'Chưa đúng rồi, không sao đâu. Cô giảng lại bằng cách khác cho ' + w + ' nhé.'
    man['finish'] = 'Chúc mừng ' + w + ', ' + w + ' đã học xong cả bài rồi!'
    man['exok'] = 'Chính xác, giỏi lắm ' + w + '!'
    for i, n in enumerate(pack['nodes']):
        for j, sg in enumerate(n['segments']): man[f'n{i}s{j}'] = sg['say']
        for lv in ['L1','L2','L3','L4']: man[f'n{i}l{lv}'] = n['levels'][lv]['say']
        for j, c in enumerate(n['checks']):
            man[f'n{i}c{j}q'] = W + ' tự làm câu này nhé: ' + re.sub(r'<[^>]+>', '', c['q'])
            man[f'n{i}c{j}e'] = 'Đúng rồi, giỏi lắm! ' + c['explain']
    mixed = pack['id'] in MIXED_PACKS
    out = {}
    for k, v in man.items():
        t = expand_abbr(unit_to_words(abs_to_words(minus_to_words(frac_to_words(struct_to_words(name_pron(array_to_words(v))))))))  # SB_NAMEPRON: ten rieng doi truoc — "Py-ta-go" ma de minus cham vao truoc se thanh "tru" # SB_ARRAYWORDS_20261109: bang array doc truoc moi lop khac
        if not mixed: t = angle_to_words(t)  # goi Anh: angle chi ap len doan Viet trong gen_mixed, khong dung doan Anh
        t = strip_parens_spoken(t)  # SB_DEPAREN_20261101: lop cuoi — bo dau ngoac con sot truoc khi vao TTS
        out[k] = t
    return out

_ONES=['không','một','hai','ba','bốn','năm','sáu','bảy','tám','chín']
def _numw(n):
    if n<10: return _ONES[n]
    if n<100:
        if n<20:
            if n==10: return 'mười'
            u=n-10
            return 'mười '+('lăm' if u==5 else _ONES[u])
        t,u=divmod(n,10)
        s=_ONES[t]+' mươi'
        if u==0: return s
        if u==1: return s+' mốt'
        if u==4: return s+' tư'
        if u==5: return s+' lăm'
        return s+' '+_ONES[u]
    if n>=1000:
        k,r=divmod(n,1000)
        s=_numw(k)+' nghìn'
        if r==0: return s
        if r<10: return s+' lẻ '+_ONES[r]
        return s+' '+_numw(r)
    h,r=divmod(n,100)
    s=_ONES[h]+' trăm'
    if r==0: return s
    if r<10: return s+' lẻ '+_ONES[r]
    return s+' '+_numw(r)
def _unit_after_frac(text):
    # SB_UNITWORDS2_20261101: don vi dung ngay sau PHAN SO tho "1/2 km" — frac se doi "1/2" thanh chu,
    # mat moc so nen unit_to_words (chay sau) khong con thay ky hieu; doi ky hieu thanh chu truoc, giu nguyen "1/2".
    t = re.sub(r'(\d+/\d+)(\s?)(' + _UNIT_ALT + r')(?![A-Za-zÀ-ỹ²³])',
               lambda m: m.group(1) + ' ' + _UNIT_MAP[m.group(3)], text)
    return re.sub(r'(\d+/\d+) (m|g|l)(?![A-Za-zÀ-ỹ(])',
                  lambda m: m.group(1) + ' ' + _UNIT_SINGLE[m.group(2)], t)

def frac_to_words(text):
    # SB_FRACWORDS_20261031: phan so dang 3/4 trong loi giang phai doc "ba phan tu" — TTS doc nguyen "3/4" thanh gach cheo/gach ngang
    if not text: return text
    text = _unit_after_frac(text)
    def rep(m):
        a,b=int(m.group(1)),int(m.group(2))
        if b==0: return m.group(0)
        return _numw(a)+' phần '+('tư' if b==4 else _numw(b))
    return re.sub(r'(\d+)\s*/\s*(\d+)', rep, text)

# SB_ARRAYWORDS_20261109 (thay bao 19:18 09/11, ca 4 bang 12-15 nhap T11 THU LOI
# "server closed ws" lien tiep): bang so lieu dang $\begin{array}{...}...\end{array}$
# phai doc thanh loi co nghia — ten cot + tung dong so lieu — chu khong de
# nguyen ma LaTeX cho TTS. Dong thoi sua 2 loi he thong ma bang nay phoi ra:
# (1) dau & ngan cot trong array la ky tu THO trong SSML (mkssml khong
# thoat ky tu) nen may chu Edge dong ket noi — lop thoat o edge_tts_raw
# sua rieng; (2) cac lop doc cu xe nat array: abs_to_words doc "{|c|c|}"
# thanh "gia tri tuyet doi cua c...", struct_to_words doc khoang "[10; 20)"
# thanh "mo ngoac... dong ngoac". Ham nay CHAY DAU TIEN trong moi chuoi
# loi doc tieng Viet (truoc name_pron/struct_to_words/abs_to_words) va chi
# tac dong khi co \begin{array} — van ban khong co bang tra ve nguyen ven.
# Quy uoc doc: hang dau = ten cot; moi o doc theo cot cua no:
# "Bang: <ten cot 1>, <nhan hang> <o>; <ten cot 2>, <nhan hang> <o>...".
# Khoang [a; b) / (a; b] / [a; b] / (a; b) doc "tu a den b".
_ARRAY_RE = re.compile(r'\$?\\begin\{array\}\{[^}]*\}(.*?)\\end\{array\}\$?', re.S)
_ARRAY_INTERVAL = re.compile(r'[\[(]\s*([^;\]\)]+?)\s*;\s*([^;\]\)]+?)\s*[\])]')

def _array_cell(c):
    c = str(c or '').strip().replace('&amp;', '&')
    c = re.sub(r'\\(?:text|mathrm|mathbf|mathit)\{([^}]*)\}', r'\1', c)
    c = re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', c)
    c = re.sub(r'\\[a-zA-Z]+', '', c)
    c = c.replace('{,}', ',').replace('$', '').replace('{', '').replace('}', '')
    c = _ARRAY_INTERVAL.sub(r'từ \1 đến \2', c)
    return re.sub(r'\s+', ' ', c).strip(' ,;')

def _array_read(body):
    body = str(body or '').replace('&amp;', '&')
    body = re.sub(r'\\hline|\\cline\{[^}]*\}', ' ', body)
    rows = []
    for part in re.split(r'\\\\', body):
        cells = [_array_cell(x) for x in part.split('&')]
        if any(cells):
            rows.append(cells)
    if not rows:
        return ''
    if len(rows) == 1:
        return 'Bảng: ' + ', '.join(rows[0])
    head = rows[0]
    corner = head[0] if head else ''
    groups = []
    for r in rows[1:]:
        label = r[0] if r else ''
        for j in range(1, max(len(head), len(r))):
            h = head[j] if j < len(head) else ''
            v = r[j] if j < len(r) else ''
            if not v:
                continue
            left = ' '.join(x for x in (corner, h) if x)
            right = ' '.join(x for x in (label, v) if x)
            groups.append((left + ', ' + right) if left else right)
    if not groups:
        return ''
    return 'Bảng: ' + '; '.join(groups)

def array_to_words(text):
    if not text or '\\begin{array}' not in str(text):
        return text
    t = str(text)
    def rep(m):
        read = _array_read(m.group(1))
        if not read:
            return ' '
        before = t[:m.start()][-1:]
        after = t[m.end():m.end() + 1]
        pre = '' if (not before or before.isspace()) else ' '
        end = '' if (after and after in ',;:!?.)]}…') else '.'
        post = '' if (not after or after.isspace() or after in ',;:!?.)]}…') else ' '
        return pre + read + end + post
    return _ARRAY_RE.sub(rep, t)

_NUMWORDS = r'(?:một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười)'
def _render_group(t, i):
    # t[i] la '(' hoac '[' — doc cau truc: mo ngoac / tru / dong ngoac (de quy theo cap ngoac)
    out = ['mở ngoặc ']
    stack = [t[i]]; i += 1
    while i < len(t) and stack:
        ch = t[i]
        if ch in '([':
            stack.append(ch); out.append('mở ngoặc '); i += 1
        elif ch in ')]':
            stack.pop(); out.append(' đóng ngoặc'); i += 1
        elif ch in '−-':
            out.append('trừ '); i += 1
            while i < len(t) and t[i] == ' ': i += 1
        else:
            out.append(ch); i += 1
    return ''.join(out), i

def struct_to_words(text):
    # SB_STRUCTWORDS_20261031 (thay chot 21:02): bieu thuc NGOAC LONG NHAU doc theo CAU TRUC nhu thay co doc tren lop —
    # "-[-(-4)]" doc "tru mo ngoac tru mo ngoac tru 4 dong ngoac dong ngoac", TUYET DOI khong gon thanh "am am am".
    # Kich hoat: nhom ngoac vuong [...] (luon doc cau truc) va "-()" khi trong ngoac bat dau bang dau tru/hoac long ngoac.
    # Ngoac don binh thuong nhu (+-8) giu nguyen cho quy tac so am xu ly (doc "am 8").
    if not text: return text
    t = text; out = []; i = 0; N = len(t)
    def ismin(ch): return ch in '−-'
    while i < N:
        ch = t[i]
        if ch == '[' or (ismin(ch) and i + 1 < N and t[i + 1] == '['):
            if ismin(ch): out.append('trừ '); i += 1
            r, i = _render_group(t, i); out.append(r); continue
        if ismin(ch) and i + 1 < N and t[i + 1] == '(':
            j = i + 2
            while j < N and t[j] == ' ': j += 1
            if j < N and (ismin(t[j]) or t[j] in '(['):
                out.append('trừ '); i += 1
                r, i = _render_group(t, i); out.append(r); continue
            out.append(ch); i += 1; continue
        out.append(ch); i += 1
    return ''.join(out)

def abs_to_words(text):
    # SB_ABSWORDS_20261031 (thay chot 21:02): ki hieu |A| doc "gia tri tuyet doi cua A". Chay SAU minus_to_words
    # (dau tru ben trong da thanh "am"; dau tru giua hai nhom || giu nguyen cho giong tu doc "tru").
    if not text: return text
    return re.sub(r'\|([^|]+)\|', r'giá trị tuyệt đối của \1', text)

# SB_UNITWORDS_20261101 (thay chot 21:24-21:26): ky hieu DON VI sau so phai doc DAY DU ten don vi
# ("40 km/h" -> "40 ki-lo-met tren gio", "500 N" -> "500 niu-ton", "5 cm" -> "5 xang-ti-met"...),
# TUYET DOI khong de TTS tu doc ky hieu (doc danh van/gac). Chi kich hoat khi ky hieu DUNG NGAY SAU SO
# (ke ca dinh lien "5cm" voi don vi nhieu chu cai) de khong dung bien so/diem (diem N, tham so m, ham g(x)).
# Don vi 1 chu cai (m, g, l) bat buoc co khoang trang truoc + khong dung truoc "(" — tranh bien.
# Chay SAU minus_to_words (de phep tru kieu "5 cm - 3 cm" van thay token "cm" nhu cu) va TRUOC expand_abbr.
# Ban JS unitWords trong elearning.html phai GIONG HET ban nay — sua la sua ca hai phia.
_UNIT_MULTI = [
    ('km/h', 'ki lô mét trên giờ'), ('cm/s', 'xăng ti mét trên giây'), ('km/s', 'ki lô mét trên giây'),
    ('mm/s', 'mi li mét trên giây'), ('m/s', 'mét trên giây'),
    ('km²', 'ki lô mét vuông'), ('cm²', 'xăng ti mét vuông'), ('dm²', 'đề xi mét vuông'),
    ('mm²', 'mi li mét vuông'), ('m²', 'mét vuông'),
    ('cm³', 'xăng ti mét khối'), ('dm³', 'đề xi mét khối'), ('mm³', 'mi li mét khối'), ('m³', 'mét khối'),
    ('km', 'ki lô mét'), ('hm', 'héc tô mét'), ('dam', 'đề ca mét'), ('dm', 'đề xi mét'),
    ('cm', 'xăng ti mét'), ('mm', 'mi li mét'),
    ('kg', 'ki lô gam'), ('hg', 'héc tô gam'), ('dag', 'đề ca gam'), ('mg', 'mi li gam'),
    ('ml', 'mi li lít'), ('Hz', 'héc'), ('Pa', 'pa xcan'),
    ('N', 'niu tơn'), ('J', 'jun'), ('W', 'oát'),
]
_UNIT_ALT = '|'.join(re.escape(u) for u, _ in _UNIT_MULTI)
_UNIT_MAP = dict(_UNIT_MULTI)
_UNIT_SINGLE = {'m': 'mét', 'g': 'gam', 'l': 'lít'}
def unit_to_words(text):
    if not text: return text
    t = text
    t = re.sub(r'(\d)\s?°C(?![A-Za-zÀ-ỹ])', r'\1 độ xê', t)
    t = re.sub(r'(\d)\s?°(?![A-Za-zÀ-ỹ0-9])', r'\1 độ', t)
    t = re.sub(r'(\d)\s?%(?![0-9])', r'\1 phần trăm', t)
    t = re.sub(r'(\d)(\s?)(' + _UNIT_ALT + r')(?![A-Za-zÀ-ỹ²³])',
               lambda m: m.group(1) + ' ' + _UNIT_MAP[m.group(3)], t)
    t = re.sub(r'(\d) (m|g|l)(?![A-Za-zÀ-ỹ(])', lambda m: m.group(1) + ' ' + _UNIT_SINGLE[m.group(2)], t)
    t = re.sub(r'(\d)(\s?)ha(?![A-Za-zÀ-ỹ])', r'\1 héc ta', t)
    return t

# SB_DEPAREN_20261101 (thay bao 23:35, ca Ban 10 n1s0): dau ngoac don con sot lai sau cac lop doc
# (ten mat phang "(P)"/"(ABC)", chu thich lo vao loi nhu "(hai ki hieu)", "(tinh chat 4)") ma dua
# nguyen vao TTS thi giong doc ha giong doc kieu "noi ngoac" tung cho — nghe ai ai, dut quang.
# Lop nay chay CUOI chuoi: BO DAU NGOAC, GIU NGUYEN noi dung ben trong. Ngoac cua bieu thuc dai so
# da duoc struct_to_words doc thanh "mo ngoac/dong ngoac" tu lop truoc nen khong con dau ngoac o day.
def strip_parens_spoken(text):
    if not text: return text
    def decide(m):
        c = m.group(1)
        if re.fullmatch(r'[A-Z][A-Z,; ]{0,11}', c): return c          # ten mat phang/diem: (P), (ABC), (O; R)
        if re.search(r'[a-zà-ỹ]', c) and not re.search(r'[+\-−×·=^√*/<>|]', c): return c  # chu thich bang chu: (hai ki hieu), (tinh chat 4)
        return m.group(0)                                             # ngoac bieu thuc dai so: giu nguyen nhu cu
    t = text
    for _ in range(4):
        nt = re.sub(r'\(([^()]*)\)', decide, t)
        if nt == t: break
        t = nt
    return re.sub(r' +', ' ', t)

def minus_to_words(text):
    # SB_MINUSWORDS_20261031 (thay chot 20:50): so am doc "am", phep tru doc "tru" — TUYET DOI khong de TTS doc dau tru thanh "gach ngang"
    # (ca goc: "So doi cua 4 la -4" bi doc "gach ngang bon"). Ap SAU frac_to_words nen ben phai dau tru co the la chu so HOAC tu so (hai, ba...).
    # Toan hang chu cai ben trai phep tru phai la token dung doc lap (x, AB) — chu cuoi cua tu tieng Viet (nhu "h" trong "Tinh") KHONG tinh.
    # Khong dung dau gach noi dai (en-dash, vd khoang 1-20 trong tieu de). Ban JS minusWords trong elearning.html phai GIONG HET — sua la sua ca hai phia.
    if not text: return text
    t = text
    t = re.sub(r'([0-9\u00b0\u00b2\u00b3\)\]])\s*[\u2212-]\s*(?=[0-9\(])', r'\1 trừ ', t)
    t = re.sub(r'([0-9\u00b0\u00b2\u00b3\)\]])\s*[\u2212-]\s*(?=(?:[A-Za-z]{1,3}(?![A-Za-z])|' + _NUMWORDS + r'\b))', r'\1 trừ ', t)
    t = re.sub(r'([0-9][A-Za-z])\s*[\u2212-]\s*(?=(?:[0-9\(]|[A-Za-z]{1,3}(?![A-Za-z])|' + _NUMWORDS + r'\b))', r'\1 trừ ', t)  # he so dinh chu: 4x - 8, 2x - y doc 'tru'
    t = re.sub(r'(^|,)\s*[\u2212-]\s*(?=,|$)', r'\1 trừ', t)  # liet ke day dau '+, -, +, -' doc 'cong, tru, cong, tru'
    t = re.sub(r'(^|[\s\(\[\{,;:=\+\-\*/\u00d7\u00f7<>\u2264\u2265"\u201c\u201d])([A-Za-z]{1,3})\s*[\u2212-]\s*(?=(?:[0-9\(]|[A-Za-z]{1,3}(?![A-Za-z])|' + _NUMWORDS + r'\b))', r'\1\2 trừ ', t)
    t = re.sub(r'(^|[\s\(\[\{|,;:=\+\*/\u00d7\u00f7<>\u2264\u2265"\u201c\u201d\'\u2018\u2019])(\s*)[\u2212-]\s*(?=(?:[0-9A-Za-z\(\[]|' + _NUMWORDS + r'\b))', r'\1\2âm ', t)
    t = re.sub(r'\u2212(?=[0-9])', 'âm ', t)
    return t

# SB_ABBRLIB_20261031 (thay lenh 18:51): thu vien doc tu viet tat toan hoc — moi loi DOC phai mo rong thanh dang day du
# (UCLN -> "uoc chung lon nhat"), TUYET DOI khong de TTS danh van tung chu cai. Nguon chuan: abbr_lib.json canh script nay;
# ban JS trong elearning.html (expandAbbr) phai GIONG HET ban nay — sua la sua ca hai phia.
_ABBR = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'abbr_lib.json'), encoding='utf-8'))
_ABBR_KEYS = sorted(_ABBR, key=len, reverse=True)
def expand_abbr(text):
    if not text: return text
    t = re.sub(r'(?<![A-Za-zÀ-ỹ0-9])TH\s*(\d+)', r'trường hợp \1', text)
    for k in _ABBR_KEYS:
        t = re.sub(r'(^|[^A-Za-zÀ-ỹ])' + re.escape(k) + r'(?![A-Za-zÀ-ỹ0-9])', lambda m: m.group(1) + _ABBR[k], t)
    return name_pron(t)

# SB_NAMEPRON_20261031 (thay chot 21:55): ten rieng toan hoc doc theo phien am thay chot — "Pytago" doc "Pi ta ngo"
# (chu HIEN THI giu nguyen "Pytago", chi loi doc doi). Ap ben trong expand_abbr de moi duong tieng deu qua.
# SB_NAMEBANK_20261101 (thay lenh 06:19): KHO PHAT AM TEN RIENG mo rong — moi ten rieng nuoc ngoai (nha toan hoc/khoa
# hoc co dinh li-quy tac-bat dang thuc mang ten, ten nguoi o cac mon) deu dang ky san dang doc tieng Viet trong bang
# _NAMEBANK duoi day, khoi va le tung ca nhu Pytago/Thales truoc kia. Bang chuan day du o READING_RULES.md muc 2h;
# ban JS namePron trong elearning.html phai KHOP bang nay. Ky luat: moi muc la (regex cac dang viet, dang doc);
# dang doc viet bang chu Viet, tach am tiet bang KHOANG TRANG (khong gach noi — tranh lop minus); dang viet phien am
# san chi bat buoc hoa chu cai dau khi dang khong dau co the trung tu Viet thuong (vd "Vi-ét" phai hoa V de khoi
# trung "viết"); muc ghep (Cauchy-Schwarz...) xep truoc muc don. Am dau dang doc co nguy co bi angle_to_words danh
# van (Vi/Pa/Bu/La/Na/Ga/Mi/Va/Am) da co chot _NAME_HEAD o duoi.
_NAME_L = r'(?<![A-Za-zÀ-ỹ])'
_NAME_R = r'(?![A-Za-zÀ-ỹ])'
_NAMEBANK = [
    # --- ghep truoc don ---
    (r'(?i:Cauchy)[-\s](?i:Schwarz)[-\s](?i:Bunyakovsky)', 'Cô si Xoác Bu nhi a cốp xki'),
    (r'(?i:Cauchy)[-\s](?i:Schwarz)', 'Cô si Xoác'),
    (r'(?i:De\s+Moivre|Đờ[-\s]?moa[-\s]?vrơ)', 'Đờ moa vrơ'),
    # --- TOAN ---
    (r'(?i:Bunyakovsky|Bunh?iacốpxki)|Bunhia[-\s]?cốp[-\s]?xki', 'Bu nhi a cốp xki'),
    (r'(?i:Cauchy)|Cô[-\s]?si', 'Cô si'),
    (r'(?i:Schwarz)', 'Xoác'),
    (r'(?i:Euclide|Euclid|Ơ-?clít)', 'Ơ clít'),
    (r'(?i:Archimède|Archimedes|Archimede)|Ác[-\s]?si[-\s]?mét', 'Ác si mét'),
    (r'(?i:Viète|Vieta|Viete)|Vi-?[Éé]t', 'Vi ét'),
    (r'(?i:Euler)|Ơ[-\s]?le', 'Ơ le'),
    (r'(?i:Descartes)|Đề[-\s]các', 'Đề các'),
    (r'(?i:Fermat)|Féc[-\s]?ma', 'Féc ma'),
    (r'(?i:Pascal)|Pa[-\s]?xcan', 'Pa xcan'),
    (r'(?i:Gauss)|Ga[ou][-\s]?xơ', 'Gao xơ'),
    (r'(?i:Newton)|Niu[-\s]?tơn', 'Niu tơn'),
    (r'(?i:Leibniz)|Lai[-\s]?bơ[-\s]?nít', 'Lai bơ nít'),
    (r'(?i:Bernoulli|Bernulli)|Béc[-\s]?nu[-\s]?li', 'Béc nu li'),
    (r'(?i:Lagrange)|La[-\s]?grăng', 'La grăng'),
    (r'(?i:Rolle)', 'Rôn'),
    (r"(?i:L['’]h[ôo]pital|L['’]hospital|Lopital)|Lô[-\s]?pi[-\s]?tan", 'Lô pi tan'),
    (r'(?i:Taylor|Taylo)', 'Tây lo'),
    (r'(?i:Mac[-\s]?laurin)', 'Mắc lô ranh'),
    (r'(?i:Tchebychev|Chebychev|Chebyshev)|Trê[-\s]?bư[-\s]?sép', 'Trê bư sép'),
    (r'(?i:H[ée]ron)|Hê[-\s]?rông', 'Hê rông'),
    (r'(?i:Ptolemy|Ptolemaios|Ptoleme)', 'Tô lê mê'),
    (r'(?i:Ceva)|Xê[-\s]?va', 'Xê va'),
    (r'(?i:Menelaus|Menelaos|Ménélaüs)', 'Mê nê la uýt'),
    (r'(?i:Fibonacci|Fibonaci)|Phi[-\s]?bô[-\s]?na[-\s]?xi', 'Phi bô na xi'),
    (r'(?i:Napol[ée]on)|Na[-\s]?pô[-\s]?lê[-\s]?ông', 'Na pô lê ông'),
    (r'(?i:Venn)', 'Ven'),
    (r'(?i:Cramer|Kra[-\s]?me|Cra[-\s]?me)', 'Cờ ra me'),
    (r'(?i:Horner)|Hoóc[-\s]?ne', 'Hoóc ne'),
    (r'(?i:B[ée]zout)|Bê[-\s]?du', 'Bê du'),
    (r'(?i:Moivre)', 'Moa vrơ'),
    (r'(?i:Dirichlet|Dirichle)|Đi[-\s]?rich[-\s]?lê', 'Đi rich lê'),
    (r'(?i:Cantor)', 'Can to'),
    (r'(?i:Abel)|A[-\s]ben', 'A ben'),
    (r'(?i:Galois)|Ga[-\s]?loa', 'Ga loa'),
    (r'(?i:Möbius|Mobius|Moebius)', 'Mô bi út'),
    (r'(?i:Stewart)', 'Stiu ớt'),
    (r'(?i:Miquel)', 'Mi ken'),
    (r'(?i:Varignon)', 'Va ri nhông'),
    (r'(?i:Simpson|Simson)', 'Sim sơn'),
    (r'(?i:Jensen)|Gien[-\s]?xen', 'Gien xen'),
    (r'(?i:Minkowski)|Min[-\s]?cốp[-\s]?xki', 'Min cốp xki'),
    (r'(?i:Hölder|Holder|Hoelder)', 'Hôn đe'),
    (r'(?i:Gay[-\s]Lussac)', 'Gay Lú xác'),
    # --- LY (ten nguoi gan don vi/dinh luat) ---
    (r'(?i:Joules?)', 'Jun'),
    (r'(?i:Watts?)', 'Oát'),
    (r'(?i:Ohms?)', 'Ôm'),
    (r'(?i:Amp[èe]re)|Am[-\s]pe', 'Am pe'),
    (r'(?i:Volta|Volts?)', 'Vôn'),
    (r'(?i:Faraday)|Pha[-\s]?ra[-\s]?đây', 'Pha ra đây'),
    (r'(?i:Hertz)', 'Héc'),
    # --- HOA ---
    (r'(?i:Mendeleev|Mendeleiev)|Men[-\s]?đê[-\s]?lê[-\s]?ép', 'Men đê lê ép'),
    (r'(?i:Avogadro)|A[-\s]?vô[-\s]?ga[-\s]?đrô', 'A vô ga đrô'),
    (r'(?i:Dalton)|Đan[-\s]?tôn', 'Đan tôn'),
    (r'(?i:Boyle)|Bôi[-\s]?lơ', 'Bôi lơ'),
    (r'(?i:Mariotte)|Ma[-\s]?ri[-\s]?ốt', 'Ma ri ốt'),
    (r'(?i:Charles)|Sác[-\s]?lơ', 'Sác lơ'),
    # --- SINH ---
    (r'(?i:Darwin)|Đác[-\s]?uyn', 'Đác uyn'),
    (r'(?i:Mendel)|Men[-\s]?đen', 'Men đen'),
    (r'(?i:Lamarck)|Lác[-\s]?mác', 'Lác mác'),
    (r'(?i:Pavlov)|Páp[-\s]?lốp', 'Páp lốp'),
    # --- DON VI DO (T7-Y 01/11): ten don vi viet gach noi bi lop minus doc thanh "tru" (ca gioi thieu
    # bai "Ki-lô-gam" doc "Ki tru lo-gam", "Mi-li-met" doc "Mi tru li-met") — doi sang dang doc cach am tiet
    # ngay tu name_pron (dau chuoi) nhu cac ten rieng; phia web sinh lai bang tu gen_namebank_js.py.
    (r'(?i:Ki[-]lô[-]gam)', 'Ki lô gam'),
    (r'(?i:Ki[-]lô[-]mét)', 'Ki lô mét'),
    (r'(?i:Đề[-]xi[-]mét)', 'Đề xi mét'),
    (r'(?i:Xăng[-]ti[-]mét)', 'Xăng ti mét'),
    (r'(?i:Mi[-]li[-]mét)', 'Mi li mét'),
    (r'(?i:Mi[-]li[-]lít)', 'Mi li lít'),
    (r'(?i:Mi[-]li[-]gam)', 'Mi li gam'),
    (r'(?i:Héc[-]ta)', 'Héc ta'),
]
_NAMEBANK_RE = [(re.compile(_NAME_L + body + _NAME_R), reading) for body, reading in _NAMEBANK]
def name_pron(text):
    if not text: return text
    t = re.sub(r'(?<![A-Za-zÀ-ỹ])(Pythagoras|Py-?tha-?go-?re|Py-?ta-?go)(?![A-Za-zÀ-ỹ])', 'Pi ta ngo', text, flags=re.I)
    # SB_NAMEPRON bo sung 01/11 (thay chot 06:05, ca goi cd-toan8-thales-dong-dang): "Thalès" doc "Ta lét" — ap moi dang
    # viet Thalès/Thales/Ta-lét/Talet; dang doc viet "Ta lét" KHONG gach noi de lop minus khong cham vao (cung bay
    # "Py-ta-go"); token "Ta" duoc bao ve khoi danh van o angle_to_words nhu token "Pi".
    # Bo sung cung dot NAMEBANK: hai dang viet lech dau "Thalét/Talét" (phia web da co) de hai phia khop nhau.
    t = re.sub(r'(?<![A-Za-zÀ-ỹ])(Thalès|Thales|Thalét|Ta-?lét|Ta-?let|Talét|Talet)(?![A-Za-zÀ-ỹ])', 'Ta lét', t, flags=re.I)
    for pat, reading in _NAMEBANK_RE:
        t = pat.sub(reading, t)
    return t

# SB_ANGLEWORDS_20261031 (thay lenh 19:52 + 19:58): ten HINH HOC trong loi DOC phai DANH VAN tung chu cai theo ten
# chu tieng Viet — goc AOC doc "a o xe", tia Ot doc "o te" (TUYET DOI khong de TTS doc "ot"), doan AB doc "a be".
# Ap dung CUNG TANG voi frac_to_words/expand_abbr: manifest() ap cho moi goi KHONG tron ngon ngu; rieng 2 goi Anh
# (MIXED_PACKS) chi ap len doan tieng Viet do split_lang tach ra trong gen_mixed — khong dung doan tieng Anh.
LETTER_VN = {'A':'a','B':'bê','C':'xê','D':'đê','E':'e','F':'ép','G':'giê','H':'hát','I':'i','J':'di','K':'ca','L':'lờ','M':'mờ','N':'nờ','O':'ô','P':'pê','Q':'quy','R':'rờ','S':'ét','T':'tê','U':'u','V':'vê','X':'ích','Y':'y','Z':'dét'}
def _spell_name(tok):
    return ' '.join(LETTER_VN[c.upper()] for c in tok)
_ANGLE_CODES = {'SGK', 'SBT', 'HSG'}  # ma tai lieu — giu nguyen, khong danh van
# Tu tieng Viet THUONG, khong phai ten hinh — TTS doc nguyen van, danh van la sai han (bang chung tu ra soat 30 goi):
# "Em" (dai tu: "Em tu lam cau nay nhe"), "Ba" (so ba: "Ba cap canh", "Ba goc"), "SAI"/"BAO" (chu Viet viet hoa nhan
# manh: "la SAI?", "BAO NHIEU"). Xem bao cao ra soat kem ban va nay.
_PROTECT_TOKENS = {'Em', 'Ba', 'SAI', 'BAO'}
# SB_PAIRSWELL_20261031 (thay chot 20:06 "Danh van TAT CA ten doan nhu vay"): cap 2 chu hoa DUNG DOC LAP o bat ky dau
# cung danh van (AB = DE -> "a be = de e"), ngoai tru cap la tu viet tat thuong: "AI" (co giao AI), "HS" (hoc sinh).
_PROTECT_PAIRS = {'AI', 'HS'}
# SB_NAMEBANK: am dau (token 2 chu Hoa-thuong) cua cac dang doc ten rieng + hau to nhan dien — token dung dau dang doc
# (vd "Vi" trong "Vi ét", "Pa" trong "Pa xcan") KHONG phai ten tia nen khong danh van khi dung truoc dung hau to nay.
_NAME_HEAD = {'Pi': ' ta', 'Ta': ' lét', 'Vi': ' ét', 'Pa': ' xcan', 'Bu': ' nhi', 'La': ' grăng', 'Na': ' pô', 'Ga': ' loa', 'Mi': ' ken', 'Va': ' ri', 'Am': ' pe'}
_GEO_WORDS = ('góc', 'tia', 'đoạn', 'đường thẳng', 'tam giác')  # ngu canh hinh hoc cho quy tac (b): "va + 1 chu hoa"
_TOK_RE = re.compile(r'(?<![A-Za-zÀ-ỹ])[A-Za-z]{1,3}(?![A-Za-zÀ-ỹ])')
_PREV_PHRASES = ('tam giác', 'tứ giác', 'hình thang', 'đường thẳng', 'đoạn thẳng')
_PROTECT_PHRASES = ('tam giác', 'tứ giác', 'hình thang')  # dung truoc ten tam/tu giac: ABC la TEN HINH, khong phai ten goc
# Ten DIEM dung mot minh (diem A, "hai diem A va B") chi la ten diem — phan biet voi ten GOC 1 chu (dung sau "goc")
# va ten DINH duoc ke trong cau hinh hoc (vd dinh dung sau "tai": "vuong tai A va D", "tai B va C"). Quy tac (b) chi
# chan rieng dang "hai diem A va B" (token dung truoc "va" la ten diem 1 chu sau chu "diem") — cac dang con lai danh van
_POINT_PREV = {'điểm'}
def _prev_ctx(text, start):
    # Cum tu + tu dung ngay truoc token (chi cach boi khoang trang), khong phan biet hoa thuong ("Tam giác", "Đoạn" dau cau)
    stripped = text[:start].rstrip()
    low = stripped.lower()
    phrase = None
    for ph in _PREV_PHRASES:
        if low.endswith(ph) and (len(low) == len(ph) or not low[-len(ph) - 1].isalpha()):
            phrase = ph; break
    m = re.search(r'([A-Za-zÀ-ỹ]+)$', stripped)
    return phrase, (m.group(1).lower() if m else None)
def angle_to_words(text):
    if not text: return text
    has_ctx = ('góc' in text) or ('tia' in text)  # ngu canh bai hinh hoc cho quy tac (d)
    has_geo = any(w in text for w in _GEO_WORDS)  # ngu canh hinh hoc rong hon cho quy tac (b)
    def clause_has_triangle(start):
        # Menh de hien tai (tu dau cau/. ; : ! ? gan nhat) co ky hieu △ hoac cum "tam giac..." thi token 3 chu la TEN
        # TAM GIAC (ke ca dang "△ABC" va dang noi tiep "tam giac ABC va ADC") — ngoai pham vi ten goc, giu nguyen.
        seg = re.split(r'[.;:!?\n]', text[:start])[-1].lower()
        return ('△' in seg) or any(ph in seg for ph in _PROTECT_PHRASES)
    def decide(m):
        tok = m.group(0)
        if tok in _PROTECT_TOKENS: return tok
        phrase, prev = _prev_ctx(text, m.start())
        if tok == 'Oxy': return _spell_name(tok)  # (c) he truc Oxy -> "ô ích y"
        if len(tok) == 3:
            if tok in _ANGLE_CODES: return tok
            if re.fullmatch(r'[A-Z]{3}', tok) or re.fullmatch(r'[a-z][A-Z][a-z]', tok):
                if phrase in _PROTECT_PHRASES: return tok  # "tam giác ABC": giu nguyen
                if prev != 'góc' and clause_has_triangle(m.start()): return tok  # "△ABC", "tam giác ABC và ADC"
                return _spell_name(tok)  # ten goc 3 chu: AOC, xOy, xOt, tOy...
            return tok
        if len(tok) == 2:
            if prev == 'tia': return _spell_name(tok)  # (a) tia Ot / tia Ox / tia OA
            if re.fullmatch(r'O[xyztmnp]', tok): return _spell_name(tok)  # (b) Ox Oy Oz Ot Om On Op o bat ky dau
            if re.fullmatch(r'[A-Z]{2}', tok):
                if tok in _PROTECT_PAIRS: return tok  # AI (co giao AI), HS (hoc sinh): viet tat, khong danh van
                if phrase in _PROTECT_PHRASES: return tok  # cap nam trong ngu canh tam giac: giu nguyen nhu quy tac truoc
                return _spell_name(tok)  # (a) MO RONG 20:06: cap 2 chu hoa dung doc lap o BAT KY dau (ke ca "AB = DE",
                # "canh huyen BC = EF", "viet AB = 5 cm", ten thu hai sau "va"/dau phay "duong thang AB va CD", "hai canh AB, AC")
                # — bao phu ca (e) cu: doan AB / doan thang AB / duong thang MN / canh BC
            if re.fullmatch(r'[A-Z][a-z]', tok) and tok != 'Do' and has_ctx:
                _suf = _NAME_HEAD.get(tok)
                if _suf and text[m.end():m.end()+len(_suf)].lower() == _suf:
                    return tok  # SB_NAMEPRON/NAMEBANK: am dau cum ten rieng da doi ("Pi ta ngo", "Ta lét", "Vi ét"...) — khong phai ten tia, khong danh van
                return _spell_name(tok)  # (d) cap Hoa-thuong trong bai co goc/tia; "Do" (Do do...) la NGOAI LE cung
            return tok
        if tok.isupper() and (prev == 'góc' or (prev == 'tại' and has_geo)): return _spell_name(tok)  # goc/dinh 1 chu:
        # "goc B" -> "goc be"; "vuong tai A", "can tai A" — ten dinh sau "tai" trong cau hinh hoc cung danh van
        if prev == 'và' and tok.isupper() and has_geo:
            before = text[:m.start()].rstrip()
            head2 = before.rsplit(' và', 1)[0].strip().lower() if ' và' in before else ''
            prevtok = head2.rsplit(' ', 1)[-1] if head2 else ''
            if (' điểm ' + prevtok + ' ') not in (' ' + head2 + ' '):
                return _spell_name(tok)  # (b) "goc B va C" -> B da danh van, C danh van not ("vuong tai B va C" cung vay)
            # chi chan dang "hai diem A va B": token dung truoc "va" la ten diem sau chu "diem" — do la ten diem, giu nguyen
        return tok
    return _point_names(_TOK_RE.sub(decide, text))

# SB_POINTNAME_20261031 (thay chot 21:32 "Diem O doc la O, OB doc la O Be, diem D doc la de, CD doc la xe de"):
# ten DIEM 1 chu dung sau cac tu dan hinh hoc phai doc bang TEN CHU tieng Viet nhu ten goc/tia/doan — truoc day
# chi "goc X"/"tai X" duoc danh van, con "tam O", "diem D", "cach O", "thi H la..." bi bo tho cho TTS tu doc
# (O bi doc "ou", D bi doc "de" con de). Ban JS _pointNames trong elearning.html phai GIONG HET.
_POINT_INTRO = r'(?:[Đđ]iểm|[Tt]âm|[Tt]ại|[Đđ]ỉnh|[Cc]ách|[Tt]ới|[Qq]ua|[Tt]hì|[Tt]ừ|[Đđ]ến|[Gg]iữa|[Dd]ây)'
def _point_names(t):
    t = re.sub(r'(^|\s)(' + _POINT_INTRO + r'\s+)([A-Z])(?![A-Za-zÀ-ỹ])',
               lambda m: m.group(1) + m.group(2) + LETTER_VN.get(m.group(3), m.group(3)), t)
    def cont(m):
        back = t[max(0, m.start() - 30):m.start()].lower()
        di = back.rfind('điểm')
        if di < 0: return m.group(0)
        seg = back[di:]
        if any(w in seg for w in ('góc', 'tại', 'đoạn', 'tia', 'đường')): return m.group(0)
        return m.group(1) + LETTER_VN.get(m.group(2), m.group(2))
    t = re.sub(r'((?:và|,|;)\s+)([A-Z])(?![A-Za-zÀ-ỹ])', cont, t)
    t = re.sub(r'(?<![A-Za-zÀ-ỹ])([A-Z])(?=\s+là\s+(?:trung điểm|tâm|đỉnh|điểm|chân|giao điểm))',
               lambda m: LETTER_VN.get(m.group(1), m.group(1)), t)
    return t

# SB_MIXLANG_20261031 (thay lenh 19:45): cau/ doan TIENG ANH trong loi giang phai thu bang GIONG ANH (tl=en),
# TUYET DOI khong de giong Viet doc nua voi. CHI ap dung cho 2 goi tieng Anh trong MIXED_PACKS; goi khac giu nguyen.
# Tieu chi doan Anh (xac dinh theo tung TU, dau cau o giua khong cat run):
#  - run tu Latin (toan chu ASCII) nam TRONG ngoac kep "..." hoac “...” (ke ca run 1 tu — trong bai Anh, tu trong
#    ngoac kep chinh la tu/cau mau tieng Anh; mau tron nhu "There are + so + do vat" duoc cat tiep o muc tu);
#  - hoac run Latin lien tuc >= 3 tu o ngoai ngoac kep (vd cau mau viet tran "A teacher works at a ...").
# Chu Viet khong dau (anh, em, trong...) cung la chu ASCII nen NGOAI ngoac kep phai >= 3 tu moi tinh, tranh bat oan.
MIXED_PACKS = {'cd-anh3-numbers-colors', 'cd-anh4-family-jobs'}
_WORD_RE = re.compile(r"[A-Za-zÀ-ỹ]+(?:['’\-][A-Za-zÀ-ỹ]+)*|\d+")
_LATIN_RE = re.compile(r"^[A-Za-z]+(?:['’\-][A-Za-z]+)*$")
_QTRANS = {ord(c): ' ' for c in '"\u201c\u201d'}
def _quote_states(text):
    st = []; cur = False
    for ch in text:
        st.append(cur)
        if ch == '"': cur = not cur
        elif ch == '\u201c': cur = True
        elif ch == '\u201d': cur = False
    return st
def split_lang(text):
    # -> [(lang, doan_text)] theo dung thu tu; lang in {'vi','en'}
    # Tu Latin la Anh khi: (a) nam trong ngoac kep, hoac (b) thuoc run Latin >= 3 tu (cau tran ngoai ngoac kep).
    # Run khong keo tu Viet dung sat ngoac kep sang (vd "mau cam — orange": 'cam' o ngoai ngoac kep, run chi 2 tu -> Viet).
    # Chu so luon thuoc phia Viet (vd "so 8 — eight": so 8 do giong Viet doc).
    if not text: return [('vi', text)]
    qst = _quote_states(text)
    words = []
    for m in _WORD_RE.finditer(text):
        w = m.group(0)
        words.append((m.start(), m.end(), bool(_LATIN_RE.match(w)), qst[m.start()] and not w[0].isdigit()))
    if not words: return [('vi', text)]
    en = set(j for j, wd in enumerate(words) if wd[2] and wd[3])
    run = []
    def flush():
        if len(run) >= 3: en.update(run)
        del run[:]
    for j, wd in enumerate(words):
        if wd[2]: run.append(j)
        else: flush()
    flush()
    segs = []; prev = 0
    for j, (s, e, lat, q) in enumerate(words):
        lang = 'en' if j in en else 'vi'
        piece = text[prev:e]
        if segs and segs[-1][0] == lang: segs[-1][1] += piece
        else: segs.append([lang, piece])
        prev = e
    if prev < len(text): segs[-1][1] += text[prev:]
    out = []
    for lang, t in segs:
        t = re.sub(r'\s+', ' ', t.translate(_QTRANS)).strip()
        if t: out.append((lang, t))
    return out if out else [('vi', text)]
def gen_mixed(path, text):
    # Thu tung doan theo ngon ngu (vi: tl=vi cham 0.92x; en: tl=en cham nhe 0.95x), ghep ffmpeg + 120ms lang o moi
    # diem doi ngon ngu. Tra False neu text khong co doan Anh nao -> caller dung duong cu.
    segs = [(lang, strip_parens_spoken(angle_to_words(unit_to_words(minus_to_words(array_to_words(t))))) if lang == 'vi' else t) for lang, t in split_lang(text)]  # + SB_DEPAREN_20261101  # SB_ANGLEWORDS + SB_MINUSWORDS: chi doan Viet # SB_ARRAYWORDS_20261109: bang array doc truoc moi lop khac
    if not any(lang == 'en' for lang, _ in segs): return False
    tmpdir = path + '.parts'; os.makedirs(tmpdir, exist_ok=True)
    try:
        sil = os.path.join(tmpdir, 'sil.mp3')
        subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','anullsrc=r=24000:cl=mono','-t','0.12','-codec:a','libmp3lame','-q:a','4',sil], capture_output=True)
        parts = []
        for i, (lang, t) in enumerate(segs):
            raw = os.path.join(tmpdir, 's%d.raw.mp3' % i)
            open(raw, 'wb').write(b''.join(fetch(c, lang) for c in chunks(t)))
            out = os.path.join(tmpdir, 's%d.mp3' % i)
            tempo = '0.92' if lang == 'vi' else '0.95'
            r = subprocess.run(['ffmpeg','-y','-v','error','-i',raw,'-filter:a','atempo=' + tempo,'-codec:a','libmp3lame','-q:a','4',out], capture_output=True)
            if r.returncode != 0 or not os.path.exists(out): raise RuntimeError('ffmpeg seg fail ' + lang)
            parts.append(out)
        lst = os.path.join(tmpdir, 'list.txt')
        with open(lst, 'w') as f:
            for i, p in enumerate(parts):
                if i: f.write("file '%s'\n" % sil)
                f.write("file '%s'\n" % p)
        r = subprocess.run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',lst,'-codec:a','libmp3lame','-q:a','4',path], capture_output=True)
        if r.returncode != 0 or not os.path.exists(path): raise RuntimeError('ffmpeg concat fail')
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

S = requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
def chunks(text, lim=175):
    parts = re.split(r'(?<=[.!?…])\s+', text.strip()); out, cur = [], ''
    for p in parts:
        while len(p) > lim:
            cut = p.rfind(' ', 0, lim)
            if cut < 40: cut = lim
            out.append(p[:cut]); p = p[cut:].lstrip()
        if len(cur) + len(p) + 1 <= lim: cur = (cur + ' ' + p).strip()
        else: out.append(cur); cur = p
    if cur: out.append(cur)
    return [c for c in out if c]
def fetch(text, lang='vi'):
    minb = 1500 if lang == 'vi' else 300
    for attempt in range(4):
        try:
            r = S.get('https://translate.google.com/translate_tts', params={'ie':'UTF-8','tl':lang,'client':'tw-ob','q':text}, timeout=30)
            if r.status_code == 200 and len(r.content) > minb: return r.content
        except Exception: pass
        time.sleep(2 + attempt*2)
    raise RuntimeError('tts fail: ' + text[:40])
def gen(packfile):
    pack = json.load(open(packfile, encoding='utf-8'))
    man = manifest(pack)
    outdir = os.path.expanduser('~/workspace/elearn/audio/' + pack['id']); os.makedirs(outdir, exist_ok=True)
    ok = fail = 0
    for key, text in man.items():
        path = os.path.join(outdir, key + '.mp3')
        if os.path.exists(path) and os.path.getsize(path) > 3000: ok += 1; continue
        try:
            if pack['id'] in MIXED_PACKS and gen_mixed(path, text):
                ok += 1
                time.sleep(0.25)
                continue
            if pack['id'] in MIXED_PACKS: text = angle_to_words(unit_to_words(minus_to_words(array_to_words(text))))  # text thuan Viet trong goi Anh: van ap quy tac ten hinh hoc + so am # SB_ARRAYWORDS_20261109
            data = b''.join(fetch(c) for c in chunks(text))
            tmp = path + '.raw.mp3'; open(tmp, 'wb').write(data)
            r = subprocess.run(['ffmpeg','-y','-v','error','-i',tmp,'-filter:a','atempo=0.92','-codec:a','libmp3lame','-q:a','4',path], capture_output=True)
            if r.returncode != 0 or not os.path.exists(path): os.replace(tmp, path)
            else: os.remove(tmp)
            ok += 1
        except Exception as e:
            fail += 1; print('FAIL', pack['id'], key, e)
        time.sleep(0.25)
    print(pack['id'], 'audio ok', ok, 'fail', fail, 'of', len(man))
# ==================== SB_TIMEMAP_20261031: che do --timings — DO THOI GIAN NOI THAT tung manh cau ====================
# Thay bao chu va tieng lech nhau (web uoc luong theo so chu khong khop toc do doc that: doan toan so doc cham thi
# chu chay truoc tieng). Che do nay cat TEXT HIEN THI goc cua tung key thanh cac manh theo ranh gioi cau, thu TTS
# RIENG tung manh ra file tam (KHONG ghi de file MP3 chinh thuc), do ffprobe, quy doi ban cham 0.92x (chia 0.92),
# cong don startSec, ghi pack['tm'][key] = [[offChars, lenChars, startSec, durSec], ...] de web lat trang chu/chay
# buoc bang theo thoi gian noi that. Chi ghi key co tu 2 manh tro len. Chay: python3 gen_pack_audio.py --timings <pack.json> [...]
import tempfile
def manifest_display(pack):
    # TEXT HIEN THI goc cua tung key — dung la dau vao ma manifest() bien doi thanh loi noi (frac -> abbr -> angle).
    man = {}
    w = 'em' if pack.get('grade', 3) >= 6 else 'con'
    W = 'Em' if w == 'em' else 'Con'
    man['intro'] = 'Chào ' + w + '! Cô là Cô Sao. Hôm nay cô trò mình cùng học bài: ' + pack['title'] + '. Bấm Nghe cô giảng để bắt đầu nào!'
    man['diag'] = 'Không sao đâu ' + w + ', ai học cũng có chỗ chưa hiểu. ' + W + ' bấm vào câu giống ý ' + w + ' nhất, cô sẽ giảng lại bằng cách khác.'
    man['retry'] = 'Chưa đúng rồi, không sao đâu. Cô giảng lại bằng cách khác cho ' + w + ' nhé.'
    man['finish'] = 'Chúc mừng ' + w + ', ' + w + ' đã học xong cả bài rồi!'
    man['exok'] = 'Chính xác, giỏi lắm ' + w + '!'
    for i, n in enumerate(pack['nodes']):
        for j, sg in enumerate(n['segments']): man[f'n{i}s{j}'] = sg['say']
        for lv in ['L1','L2','L3','L4']: man[f'n{i}l{lv}'] = n['levels'][lv]['say']
        for j, c in enumerate(n['checks']):
            man[f'n{i}c{j}q'] = W + ' tự làm câu này nhé: ' + re.sub(r'<[^>]+>', '', c['q'])
            man[f'n{i}c{j}e'] = 'Đúng rồi, giỏi lắm! ' + c['explain']
    return man

_SENT_END_RE = re.compile(r'[.!?…]+(?=\s|$)')
def split_fragments(text, maxlen=90, minlen=12):
    # Cat thanh cac manh LIEN TUC phu kin toan bo text; tra ve [(offChars, lenChars)] tinh tren text goc.
    if not text: return []
    spans = []
    start = 0
    for m in _SENT_END_RE.finditer(text):
        if m.end() > start: spans.append([start, m.end()])
        start = m.end()
    if start < len(text): spans.append([start, len(text)])
    # Manh dai hon maxlen: cat them tai ';' hoac ',' gan giua nhat (moi nua >= 15 ky tu), lap lai den khi on.
    changed = True
    while changed:
        changed = False
        out = []
        for s, e in spans:
            cut = None
            if e - s > maxlen:
                mid = (s + e) / 2.0
                for mm in re.finditer(r'[;,](?=\s)', text[s:e]):
                    c = s + mm.end()
                    if c - s >= 15 and e - c >= 15 and (cut is None or abs(c - mid) < abs(cut - mid)): cut = c
            if cut: out += [[s, cut], [cut, e]]; changed = True
            else: out.append([s, e])
        spans = out
    # Manh ngan hon minlen: gop voi manh ke (uu tien manh truoc; manh dau ngan thi gop vao manh sau).
    merged = []
    for s, e in spans:
        if merged and e - s < minlen: merged[-1][1] = e
        else: merged.append([s, e])
    if len(merged) > 1 and merged[0][1] - merged[0][0] < minlen:
        merged[1][0] = merged[0][0]; merged.pop(0)
    return [(s, e - s) for s, e in merged]

def _mp3_duration(path):
    r = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0', path], capture_output=True, text=True)
    return float(r.stdout.strip())

_TM_CALLS = [0]
def _measure_spoken(spoken):
    # Thu TTS rieng manh nay ra file tam, do thoi luong tho roi quy doi sang ban cham 0.92x giong ffmpeg atempo=0.92.
    data = b''
    for c in chunks(spoken):
        data += fetch(c); _TM_CALLS[0] += 1
        time.sleep(float(os.environ.get('TM_PACE', '0.15')))  # SB_TIMEMAP: driver dat TM_PACE lon hon khi nhieu doi cung goi TTS
    fd, tmp = tempfile.mkstemp(suffix='.mp3'); os.close(fd)
    try:
        with open(tmp, 'wb') as f: f.write(data)
        return _mp3_duration(tmp) / 0.92
    finally:
        try: os.remove(tmp)
        except OSError: pass

def gen_timings(packfile, force=False):
    pack = json.load(open(packfile, encoding='utf-8'))
    pid_ = pack['id']
    if pid_ in MIXED_PACKS:
        print(pid_, 'SKIP (goi tron ngon ngu Anh — dot nay chua do)'); return
    disp = manifest_display(pack)
    tm = dict(pack.get('tm') or {})
    audiodir = os.path.expanduser('~/workspace/elearn/audio/' + pid_)
    done = flagged = skipped = 0
    for key, text in disp.items():
        frags = split_fragments(text)
        if len(frags) < 2: continue
        if key in tm and not force: skipped += 1; continue
        rows = []; tcur = 0.0; okk = True
        for off, ln in frags:
            spoken = strip_parens_spoken(angle_to_words(expand_abbr(unit_to_words(abs_to_words(minus_to_words(frac_to_words(struct_to_words(name_pron(array_to_words(text[off:off + ln]))))))))))  # + SB_DEPAREN_20261101 # SB_ARRAYWORDS_20261109: bang array doc truoc moi lop khac
            try: dur = _measure_spoken(spoken)
            except Exception as e:
                print('  MEASURE FAIL', pid_, key, repr(e)); okk = False; break
            rows.append([off, ln, round(tcur, 3), round(dur, 3)]); tcur += dur
        if not okk: continue
        offp = os.path.join(audiodir, key + '.mp3')
        if os.path.exists(offp):
            real = _mp3_duration(offp)
            if real > 0 and abs(tcur - real) / real > 0.10:
                flagged += 1
                print('  FLAG %s %s: tm=%.2fs vs mp3=%.2fs (lech %.0f%%)' % (pid_, key, tcur, real, abs(tcur - real) / real * 100))
        tm[key] = rows; done += 1
        pack['tm'] = tm  # ghi local ngay tung key: chay lai sau su co khong phai do lai tu dau
        with open(packfile, 'w', encoding='utf-8') as f: json.dump(pack, f, ensure_ascii=False, indent=1)
        print('  tm %s %s: %d manh, tong %.2fs' % (pid_, key, len(rows), tcur))
    print(pid_, 'tm keys moi:', done, '| flagged:', flagged, '| bo qua (da co tm):', skipped, '| tong so lan goi TTS:', _TM_CALLS[0])
    if done:
        pack['tm'] = tm
        with open(packfile, 'w', encoding='utf-8') as f:
            json.dump(pack, f, ensure_ascii=False, indent=1)
        r = subprocess.run(['python3', os.path.expanduser('~/workspace/tools/r2_s3.py'), 'put', 'elearn-packs/' + pid_ + '.json', packfile, 'application/json'], capture_output=True, text=True)
        print(pid_, 'push R2:', (r.stdout or r.stderr).strip())

if __name__ == '__main__':
    _args = sys.argv[1:]
    if _args and _args[0] == '--timings':
        for f in _args[1:]: gen_timings(f)
    else:
        for f in _args: gen(f)
