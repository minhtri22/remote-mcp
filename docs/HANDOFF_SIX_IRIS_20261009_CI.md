# Bàn giao — Kiểm định và phát hành

Bản ứng viên thuộc PR #63, kết hợp công việc PR #58–#62. Commit đã kiểm định: `11b6283e8dd1a2fefca9995378ccbad41b4a4f4e`.

**PASS 6/6 bộ kiểm thử** Windows/Linux. Đã khắc phục sai khác ký tự xuống dòng CRLF, chạy lại thành công kiểm thử Windows từng quá hạn.

**Chưa nâng cấp gateway hoặc node thật.** Khóa phiên bản node và cách ly hàng đợi mới có trong mã nguồn, chưa được kích hoạt ở production. Watchdog node vẫn trỏ phiên bản cũ. Không được suy diễn CI PASS thành SIX hoặc IRIS đã hoạt động.

Bước tiếp theo: đối soát biên nhận gateway thật rồi mới xét phát hành có bảo vệ tiến trình.
