# saobay-audio-worker

> Repo nhà: **saobay/tienganh6** — mã bộ thu nằm ở thư mục `audio-worker/` của repo này; hai workflow nằm ở `.github/workflows/` ở gốc repo. Thư mục `bell/` (chuông cửa) chỉ nằm trên máy SAOBAY, không nằm trong repo.

Bộ thu tiếng cho Module 14 / tab 14 của SAOBAY, chạy trên **GitHub Actions** thay cho đường thu trên máy ảo hay bị chặn ghi file.

## Cách hoạt động

- Workflow `Thu tieng Module 14 (SB_M14AUDIO)` chạy mỗi 10 phút (và bấm chạy tay được).
- `recorder.py` quét hàng chờ trên R2 `data/mod14-drafts/<uid>/index.json`, với mỗi mục đang `pending`:
  - lấy lời đọc theo đúng chuỗi luật đọc của Lớp học Cô Sao (`elearn/gen_pack_audio.py`),
  - thu MP3 bằng Edge TTS qua `elearn/edge_tts_raw.py` — giọng cô **Mẫu 3: vi-VN-HoaiMyNeural, tốc độ −20%, cao độ +0Hz**; tiếng học sinh theo bảng 4 giọng phân vai trong `recorder.py` (`KID_VOICES`),
  - đẩy MP3 lên đúng key `k` đã ghi sẵn trong bản ghi (`data/mod14-audio/<draftId>/...`),
  - đọc lại bản ghi tươi, chỉ lật `s = ready` khi hash lời đọc còn khớp, đồng thời cập nhật trường `au` trong `index.json`.
- Giao thức hàng chờ / key / trạng thái **giống hệt** bản thu cục bộ `tools/mod14_audio_recorder.py` — phía web (Module 13 / tab 14) không phải sửa gì.
- Mỗi lượt chạy ghi một bản tóm tắt vào R2 `data/gh-audio-worker/last-run.json` để kiểm tra từ bên ngoài.
- Chống chạy chồng: nhóm `concurrency` của workflow + cổng hash khi lật `ready` trên chính hàng chờ R2.

## Secrets cần cài (Settings → Secrets and variables → Actions)

| Tên secret | Giá trị |
|---|---|
| `R2_ACCESS_KEY_ID` | Access key của kho R2 `saobay-data` (nguồn: `~/.config/rclone/rclone.conf`, mục `[saobay-r2]`) |
| `R2_SECRET_ACCESS_KEY` | Secret key tương ứng |
| `R2_ENDPOINT` | Endpoint S3 của R2 (cùng nguồn trên) |

Chưa cài đủ secrets thì workflow tự bỏ qua lượt chạy (không báo lỗi đỏ).

## Chạy tay

Tab **Actions** → workflow **Thu tieng Module 14 (SB_M14AUDIO)** → **Run workflow** (có thể nhập một `uid` để chỉ thu cho tài khoản đó khi cần kiểm tra).

## Ghi chú

- Đường thu cục bộ trên máy ảo vẫn chạy song song làm dự phòng; việc tắt hẳn để sau khi bản GitHub thu thử đạt.
- Chi phí Actions ước tính: ~144 lượt/ngày × tối thiểu 1 phút ≈ 4.300 phút/tháng (repo riêng tư gói Free có 2.000 phút) — nếu cần giảm: nới lịch lên 15–30 phút/lượt.


---

## BỔ SUNG (10/11/2026): bộ thu TÊN + chuông cửa 1 phút

### Workflow thu tên học sinh — `name-audio.yml`

- Chạy `name_recorder.py`: quét học sinh còn thiếu tiếng tên trong kho
  tên duy nhất trên R2 (`data/classroom/name-audio.json` +
  `assets/classroom/names/`), thu bổ sung bằng giọng Mẫu 3 + cắt im
  lặng hai đầu, đúng quy ước khoá/slug của bộ thu tên hiện tại.
- Lịch `*/10` phút + chạy tay (có ô chỉ thu một tên để kiểm thử).
- Nguồn học sinh: bản sao `dataD1/users.json` trên R2; nếu có thêm
  secret `CF_API_TOKEN` (token Cloudflare chỉ-đọc D1) thì quét đủ
  toàn trường từ D1 như bản cục bộ. Không có token vẫn chạy, chỉ
  theo bản sao (ít học sinh hơn).
- Chống trùng với bản cục bộ chạy song song: cả hai đều không bao
  giờ thu lại khoá đã có trong index; ghi index kiểu chỉ thêm khoá
  chưa có trên bản đọc tươi. An toàn chạy đồng thời.

### Chuông cửa 1 phút — thư mục `bell/`

GitHub Actions không cho lịch dày hơn 5 phút, nên việc "có hàng chờ
là thu ngay" do một **Worker Cloudflare riêng** tên
`saobay-audio-bell` đảm nhiệm (hoàn toàn độc lập, **không đụng
Worker www của trang web**):

- Cron 1 phút/lần: ngó hàng chờ `data/mod14-drafts/` trên R2 (đọc
  index, và đọc thẳng file nháp để chắc chắn thấy mục `pending`).
- Thấy có việc → gọi `workflow_dispatch` cho `record.yml` → bộ thu
  chạy ngay. Chống bấm dồn: tối thiểu 2 phút giữa hai lần bấm.
- Hàng chờ trống: mỗi lượt chỉ tốn vài thao tác đọc R2 rẻ.
- Cần thêm MỘT việc tay: tạo **fine-grained personal access token**
  trên GitHub (chỉ repo này, quyền **Actions: Read and write**),
  rồi cài token đó làm secret `GH_PAT` cho Worker
  `saobay-audio-bell` trong Cloudflare (Workers → saobay-audio-bell
  → Settings → Variables and Secrets). Chưa có `GH_PAT` thì chuông
  chỉ ngó và ghi trạng thái, không lỗi.
- Triển khai chuông từ máy SAOBAY: `python3 bell/deploy_bell.py`
  (chỉ tạo/cập nhật script `saobay-audio-bell` + lịch cron của nó).

### Chi phí Actions — cập nhật

- Hai lịch `*/10` (clip bài + tên) = 288 lượt/ngày. Mỗi lượt dù
  không có việc cũng bị tính tối thiểu 1 phút → khoảng **8.600
  phút/tháng**, vượt hạn mức Free 2.000 phút/tháng của repo riêng
  tư. Các lượt do chuông bấm chỉ phát sinh khi thật có việc.
- Ba hướng xử lý (chủ repo chốt): (1) để repo ở chế độ công khai —
  Actions miễn phí cho repo công khai, mã nguồn không chứa khoá nào
  (khoá nằm ở Secrets); (2) giảm hai lịch chạy nền xuống `*/30`
  (khoảng 2.900 phút/tháng) và dựa vào chuông cho đường nhanh;
  (3) trả phí phút vượt. Mã hiện tại giữ lịch `*/10` như đã chốt,
  thay đổi chỉ là sửa 2 dòng cron trong 2 file workflow.
