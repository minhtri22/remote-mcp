# Bàn giao — Bằng chứng SIX và IRIS

Ngày 09/10/2026. **SIX và IRIS chưa thông thực thi.** Node trực tuyến nhưng hai proxy job SIX vẫn QUEUED, thiếu node_job_id; IRIS BLOCKED.

Bộ kiểm kê Windows ngoài băng đã được người dùng kích hoạt và tự cập nhật. PID 9152, PID cha 24528 của CQG-RU còn sống. Không dừng hoặc chạy lại.

Đã đọc 1.353 biên nhận lệnh trong cơ sở dữ liệu node ở chế độ chỉ đọc. Hai lệnh SIX gốc đều nhận và kết thúc FAILED, không tạo node job: một lệnh lỗi thiếu thư mục làm việc (cwd); lệnh kia gọi cmd.exe không thuộc danh sách cho phép (allowlist). Gateway vẫn hiện QUEUED.

Bản chứng cứ nằm dưới vùng _REMOTEMCP_MAINTENANCE của máy Windows: node-command-receipts.json và SIX_IRIS_NODE_RECEIPTS_FROZEN_20261009.json. Không ghi đè chứng cứ.

Bước tiếp theo: đọc biên nhận chính xác của hai lệnh ở máy gateway thật; không gửi lại job.