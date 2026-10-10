# V31 — 19 lease lịch sử: bản ghi quan sát chỉ đọc (2026-10-10)

**Loại:** bằng chứng vận hành, không phải kết quả khoa học, không thuộc `Lineage.md`.
**Nguồn gốc:** operator chạy `Invoke-V31-19-Lease-ReadOnly-Snapshot.ps1` trên `machine-1` lúc ~14:38 +07 ngày 2026-10-10, kiểm tra chéo bằng kết quả đọc tệp RemoteDesktop.
**Tệp chính:** `<PRIVATE_RESEARCH_ROOT>\.remotemcp\machine-1\control\v31-19-leases-20261010-143838-8ed63c72.json`.
**Snapshot time:** `1791617918286` ms; **digest SHA-256 của danh sách 19 hàng**: `9d316322454b5894a3093688d8a0b7090e221312b70153c6bf2ba038016fdd85`.
**Bản chương trình phân loại:** `tools/v31_readonly_lease_classifier.py`, Git blob `0aa45857cb8911a694497fd8e088a41856d72600`. Kiểm thử tổng hợp Windows/Linux 2/2 PASS trên PR #72.

## Các con số đã xác minh từ đủ 19 hàng

| Classification | Count | Adjudication allowed |
| --- | ---: | --- |
| `EXPIRED_NODE_TERMINAL_GATEWAY_STALE` | 15 (13 `SUCCEEDED`, 2 `FAILED`) | Evidence only; no gateway state mutation |
| `EXPIRED_DELIVERED_NO_RECEIPT` | 3 | NO. Uncertain delivery outcome |
| `EXPIRED_NODE_RECEIVED_NONTERMINAL` | 1 | NO. Ambiguous node execution lifecycle |
| `UNEXPIRED_LEASE_HOLD` | 0 | — |
| `IDENTITY_MISMATCH_HOLD` | 0 | — |
| **Total** | **19** | No replay, cleanup, or mark terminal authorized |

`actual_leased=19`, `inventory_drift=false`, `hard_safety_hold=false`, `read_only=true`, `gateway_db_mutated=false`, `node_db_mutated=false`, `historical_command_replay_authorized=false`.

**Diễn giải quan trọng:** `hard_safety_hold=false` chỉ là kết quả theo tiêu chí hẹp của bộ phân loại, **không chứng minh đã hòa giải an toàn toàn bộ**. Bốn hàng còn mơ hồ vẫn buộc `HISTORICAL_LEASE_RECONCILIATION=UNRESOLVED`. Không được dùng giá trị này làm lối tắt cho việc xóa, sửa hoặc requeue.

## Bốn hàng cần điều tra không tác động

| Command | Type | Evidence / next read-only lookup |
| --- | --- | --- |
| `cmd_263f75d83d21a301738979bedb0f8439` | `JOB_SUBMIT` | Delivery attempt 1, no `node_commands` receipt; correlate *exact* operation, job table, task ID `tsk_aa5db1b6778f05abc266749c`, provenance/worker fingerprint; no replay |
| `cmd_bc9f6cd38fdf6b765feca7ccc2608f26` | `JOB_GET` | Attempt 1, no node receipt; read-only command and job lookup only |
| `cmd_575ae5e343814a195d3f76bcac2b1803` | `PROJECT_PROBE` | Attempt 1, no node receipt; no project mutation |
| `cmd_7c85d6475d4156332e8a45d3714b5a89` | `TASK_BASE_RESOLVE` | Attempt 1, node `EXECUTING`, null `finished_at_ms`, matching route/hash/type; preserve row and investigate exact executor/OS process provenance |

Một trong 15 lệnh terminal là `TASK_SEARCH` có node `finished_at_ms` muộn hơn `command_expires_at_ms`. Đây là ví dụ gateway deadline không đồng nghĩa node hoàn thành đúng hạn. Không dùng terminal receipt để tuyên bố `<55s` nếu chưa có client elapsed và mã thời gian.

## Bước kế tiếp (không mutation)

1. `REMOTEMCP_V31_NODE_POLL_CADENCE_INSTRUMENTATION_AND_19_LEASE_READONLY_CLASSIFIER_IMPLEMENTATION_STATIC_PREFLIGHT`: instrumentation per-phase chỉ trong code branch; test Windows/Linux, independent comparison, no production node update.
2. Đối chiếu phụ riêng 4 hàng mơ hồ với durable job/node event/OS process evidence bằng phép đọc kiểm soát; yêu cầu báo `UNRESOLVED` nếu chứng cứ thiếu. Không dùng tên `RECONCILED` trước khi có adjudication riêng.
3. Chỉ sau QA và authorization độc lập mới mở production live 6x read check, dừng ngay khi lượt đầu FAIL.

**Production at observation:** gateway `ef9f81f05ea2be9116751280340c47bd05d908ad` PASS; pinned node `dabce9a76502dbae541a0eddff0096fb8dfe908a`; `LIVE_6X=NOT_PASS`; `REMOTE_MAINTENANCE_EXECUTION=NOT_PASS`; science task states untouched.
