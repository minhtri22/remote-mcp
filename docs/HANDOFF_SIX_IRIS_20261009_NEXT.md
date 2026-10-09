# Bàn giao — Trình tự bắt buộc

**Trạng thái:** PASS mã nguồn; PASS kiểm kê tiến trình; PASS chứng cứ node; HOLD chứng cứ gateway; HOLD nâng cấp thật; SIX/IRIS chưa hoạt động.

1. Xác định đúng máy chạy gateway và quyền bảo trì độc lập, không dựng gateway khác.
2. Đối chiếu hai lệnh gốc trong `device_commands` với biên nhận `node.db` đã khóa. Chỉ đọc, không xóa hay phát lại.
3. Phát hành gateway mới với khóa phiên bản node chính xác và cách ly hàng đợi khoa học. Xác minh trạng thái chặn tại `device_status`.
4. Chuẩn bị node mới, watchdog và phương án quay lui; xác minh PID 9152/24528 cả trước và sau.
5. Chỉ mở thử nghiệm hạ tầng không khoa học sau khi đối soát đạt PASS. Khôi phục SIX rồi IRIS; IRIS còn phải kiểm tra RAM trước P2.

**Bước tiếp theo:** `REMOTEMCP_GATEWAY_OOB_ACCESS_DISCOVERY_EXACT_COMMAND_RECEIPTS_AND_RELEASE_QUARANTINE_LOCK`.