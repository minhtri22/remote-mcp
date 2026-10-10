# REMOTEMCP_V31_NODE_POLL_CADENCE_AND_DURABLE_COMMAND_POLICY_RECOVERY_PREREGISTRATION

**Ngày khóa thiết kế:** 2026-10-10 · **Trạng thái:** PREREGISTRATION ONLY — không mở triển khai hay chạy thực địa mới · **Chủ sở hữu:** RemoteMCP infrastructure · **Phạm vi:** `machine-1` / gateway, node, nhật ký lệnh và kênh bảo trì. Không thuộc CQG-RU, CQG-J4.2 hoặc IRIS.

## 0. Bằng chứng và ranh giới bất biến

| Thuộc tính | Giá trị ghim |
| --- | --- |
| Gateway sản xuất sau nâng cấp | `ef9f81f05ea2be9116751280340c47bd05d908ad` — bounded HTTP poll 2 giây |
| Node được ghim, không đổi | `dabce9a76502dbae541a0eddff0096fb8dfe908a` |
| Gateway cũ (đối chứng) | `9f10cec250a9a266daa073ebd0df31ea16727f39` |
| Thiết bị | `<PINNED_DEVICE_ID>`; `machine-1`; generation `1` |
| Khóa công khai (fingerprint SHA-256) | `b657d5395e393e0957a9ed358bb5a1fe1588fde5d3a44be71295a68d1e73def9` |
| Bằng chứng đối soát đã đóng băng | `<PRIVATE_RESEARCH_ROOT>\.remotemcp\machine-1\control\v31-managed-route-post-promotion-20261010-121336-f0299cb4.json` |
| Hành vi hậu nâng gateway | Lượt `TASK_LIST_DIR` có `cmd_7bd71476d51c94ce8d39a18b859fb892`, tạo→node nhận **50.459 ms**, gateway/node cùng `SUCCEEDED`, nhưng lời gọi quản lý thất bại sau ~62,6 giây (`DEVICE_COMMAND_PENDING`) |
| Thử PowerShell bảo trì | `cmd_270b172acdfca595d84c241f4c37fcba`, node nhận sau **2.359 ms**, gateway/node cùng `FAILED/COMMAND_NOT_ALLOWED` |
| Hàng lệnh lịch sử tại bản chụp | **19 `LEASED`**, tất cả lệnh trạng thái `QUEUED/LEASED` còn hiệu lực **0** tại bản chụp vận hành trước đó; chưa có phân loại 19 bản ghi một-một |
| Bằng chứng tốc độ trước/sau (không phải đánh giá nguyên nhân) | 56 lệnh có biên nhận; trung vị tạo→nhận 6.286 ms; phân vị 90 104.271 ms; 7/56 lệnh từ 55 giây trở lên. Đây là tập lịch sử, **không** phải 56 phép thử độc lập sau nâng cấp |
| Công việc thực đang chạy theo signed node capacity | `0` (tại lần đọc xác nhận); bản ghi proxy `RUNNING/QUEUED` lịch sử tồn tại và được giữ nguyên |

**Không suy diễn:** gateway `ONLINE`/heartbeat `ONLINE` không chứng minh poll loop đang nhận lệnh; gateway terminal receipt về sau không chứng minh công cụ phía khách nhận kết quả đúng hạn. Nhật ký `HTTP 502` và `sqlite3.OperationalError: locking protocol` có tồn tại nhưng **chưa được gắn thời gian** với sự cố vừa kiểm thử. Không tuyên bố nguyên nhân độc nhất.

**Cấm trong gate này:** thay node/watchdog/Startup/`.vbs`/định danh/khóa/chữ ký; re-pair/revoke; `task_claim` task nghiên cứu; xoá/sửa `LINEAGE.md` khoa học; `JOB_SUBMIT` chạy lại; replay các `DEVICE_COMMAND_EXPIRED`; `cleanup_pending` clear; ghi vào node/gateway SQLite; bật PowerShell trong global durable allowlist; phát tín hiệu CQG-RU/J4.2 tiếp tục trước chứng cứ 6/6 PASS.

## 1. Câu hỏi kiểm định và giả thuyết đối nghịch

**Q1: Độ trễ sinh ra ở khâu nào?** Phân rã toàn bộ vòng node thành `capacity_snapshot` → `heartbeat` → `poll request` → `gateway poll dispatch` → `node receive` → `execute` → `result ACK`; tách độ trễ phía khách chờ `command.wait`. Không chỉ đo tổng thời gian.

- **H1 — heartbeat / SQLite:** thao tác `capacity_snapshot` hoặc `heartbeat` trước `poll` trì hoãn, raise hoặc làm mất kết nối. Dấu hiệu: nhịp poll lệch ngay sau heartbeat, stall tương quan `sqlite`/heartbeat duration, nhưng không nhất thiết có HTTP 502.
- **H2 — HTTP/proxy/reconnect:** request `/device/v1/poll` timeout, 502 hoặc backoff; nhịp gửi poll ngắt quãng dù heartbeat vẫn có. Dấu hiệu: transport error / HTTP status gắn `poll` và lần retry; không được log signed headers.
- **H3 — gateway command journal / lease:** poll tới đúng hạn nhưng `commands.poll` không chọn được command, do lock, lease/expiry/backlog, hoặc DB busy. Dấu hiệu: incoming poll + dispatch `none`, latency database, queue inventory; cần xác nhận bằng id khớp.
- **H4 — node execution / result path:** command đã đến, nhưng node execute/ACK lâu, do tài nguyên node hoặc mạng. Dấu hiệu `node_received_at_ms` sớm, `node_finished_at_ms` hoặc `gateway_finished_at_ms` muộn.
- **H5 — client wait budget:** thao tác cuối cùng thành công nhưng sau deadline phía client/gateway. Dấu hiệu cùng `SUCCEEDED` với `tool_elapsed_ms>=55,000`.

Tất cả giả thuyết phải đối chiếu cùng `command_id`, `device_id`, `route_generation` và `request_hash` và dòng thời gian. Chấp nhận **MULTIFACTOR** hoặc **UNRESOLVED** nếu chứng cứ chưa phân giải.

**Q2: 19 lease lịch sử có phải việc đang chạy không?** Không suy từ chữ `LEASED`. Phân loại **độc lập từng hàng** theo hạn lệnh, hạn lease, số lần giao, biên nhận `node_commands`, chữ ký/identity và trạng thái terminal. Không tự động thay trạng thái.

**Q3: Kênh bảo trì riêng phải có quyền gì?** Không chấp nhận mở `powershell.exe` hoặc `cmd.exe` toàn cục. Thiết kế năng lực bảo trì tách riêng, có tài khoản/agent/task đã được cấp phép, mã script pin, đường dẫn bị giới hạn, quyết định xét duyệt thao tác và nhật ký bất biến. Lệnh `PowerShell` bất kỳ luôn bị từ chối.

## 2. Hợp đồng đo đạc, tính toàn vẹn và quyền riêng tư

**Mốc thời gian độc lập:** mỗi bước phát `monotonic_ns` (chỉ trừ trong cùng tiến trình) và `wall_utc_ms` (để nối vết, không dùng tính latency cross-machine khi chưa kiểm tra sai lệch đồng hồ). `duration_ms=(monotonic_end_ns-monotonic_start_ns)/1e6`. Không lấy chênh timestamp từ hai máy làm latency chuẩn nếu chưa đo clock offset.

Mỗi `poll_cycle` có các trường:
`schema=remotemcp.node-poll-cadence.v1`, `cycle_seq` tăng, `node_release`, `gateway_release_seen`, `device_id`, `route_generation`, `utc_ms`, `heartbeat_due`, `capacity_duration_ms`, `process_safety_duration_ms`, `heartbeat_duration_ms`, `poll_http_duration_ms`, `poll_gap_monotonic_ms`, `poll_status` (`204/200/502/TIMEOUT/ERROR`), `reconnect_backoff_ms`, `command_id` (chỉ id không payload), `execute_duration_ms`, `result_ack_duration_ms`, `sqlite_error_code`, `exception_class`, `node_process_epoch`.

Gateway tương ứng đo:
`request_received_utc_ms`, `auth_duration_ms`, `command_poll_db_duration_ms`, `lease_transition_duration_ms`, `http_respond_duration_ms`, `gateway_command_created_utc_ms`, `wait_duration_ms`, `terminal_receipt_utc_ms`, `command_id`, `request_hash`, `delivery_attempt`.

**Bảo mật:** nhật ký chỉ được chứa IDs, mã lỗi, thời lượng, status và digest; không in `Authorization`, chữ ký, public/private key material, `lease_token`, `argv` bí mật, payload, env, path nhạy cảm, stdout/stderr của job khoa học. Có `source_sha256`, số sự kiện, bản đồ các khóa và dấu vết host trước/sau. Tệp log mới trong `<PRIVATE_RESEARCH_ROOT>\.remotemcp\machine-1\control\` với nhãn độc lập và không sửa các tệp cũ.

**Lỗi SQLite:** `sqlite3.OperationalError: locking protocol` phải được phân loại riêng (khác `SQLITE_BUSY`, `SQLITE_LOCKED`, `database is locked`, `disk I/O`). Nếu cần retry ở nhánh triển khai, chỉ thử với **read-only capacity_snapshot** và backoff hữu hạn; tuyệt đối không retry mutation, command receipt, `job_submit`, hoặc suy diễn trạng thái công việc. Trường `capacity_unresolved` phải fail-closed, không báo capacity=0 từ lỗi đọc.

**Observability gate:** bản sửa đo đạc **không đổi lịch dispatch**, retry, TTL, crypto, state machine, rate limit hay process topology. `EVENT_SCHEMA_V1` test deterministic và lưu chỉ số chi phí ghi nhật ký (overhead). Mọi tối ưu bất đồng bộ/worker isolate là đề xuất successor riêng sau khi quy nguyên nhân.

## 3. Phân loại cố định 19 LEASED theo chứng cứ vật lý

Chỉ đọc `gateway.runtime.db:device_commands` trên đúng `device_id`; nối bằng `command_id` với `node.node.db:node_commands` (read-only WAL-safe). Mỗi hàng phải xuất đủ: `command_id, command_type, task_id, route_generation, request_hash, state, delivery_attempt, created_at_ms, command_expires_at_ms, lease_expires_at_ms, node_receipt_present, node_state, node_received_at_ms, node_finished_at_ms, fingerprint_consistent, classification`. Không log `payload_json` hoặc token.

**Nhóm phân loại loại trừ lẫn nhau, ưu tiên từ trên xuống:**
1. `IDENTITY_MISMATCH_HOLD`: khác request_hash/route_generation/type, hoặc thiếu trường xác thực thiết yếu ⇒ cấm mọi thao tác.
2. `UNEXPIRED_LEASE_HOLD`: command/lease còn hiệu lực ⇒ dừng promotion/quarantine release và không động chạm.
3. `EXPIRED_NODE_TERMINAL_GATEWAY_STALE`: gateway `LEASED` hết hạn, node receipt `SUCCEEDED/FAILED/IN_DOUBT` khớp identity ⇒ giữ nguyên, cần quyết định reconcile riêng nếu mở sau.
4. `EXPIRED_NODE_RECEIVED_NONTERMINAL`: node có receipt nhưng chưa terminal ⇒ không tự hủy/đánh dấu.
5. `EXPIRED_DELIVERED_NO_RECEIPT`: `delivery_attempt>0`, không có receipt ⇒ kết quả không rõ, giữ nguyên.
6. `EXPIRED_NEVER_DELIVERED`: `delivery_attempt=0`, không có receipt ⇒ giữ nguyên.
7. `UNRESOLVED_HOLD`: dữ liệu null/garbled, nhiều hàng trùng, race hoặc DB lỗi ⇒ dừng.

Khi phân loại `LEASED`, **thời điểm hết hạn lệnh** là `command_expires_at_ms`, và **thời điểm lease hết hạn** là `lease_expires_at_ms`; không dùng một mốc thay cho mốc kia. 19 là số quan sát lúc `2026-10-10 12:13 +07` **không phải** một invariant vĩnh viễn. Snapshot mới mà số hàng khác 19 phải báo `INVENTORY_DRIFT`, so sánh, không tự sửa.

**Gate phân loại:** `read_only=true`, `original_rows_mutated=false`, `node_jobs_restarted=false`; tất cả hàng phân loại được; identity khớp nếu có receipt; tổng từng nhóm = tổng số `LEASED`; lưu `sha256` bản chụp, id và quan sát thời gian. Đặc biệt `expired` không đồng nghĩa `safe to replay`.

## 4. Kênh bảo trì giới hạn năng lực (thiết kế, chưa kích hoạt)

Giữ dự án `prj_5d111640140f57b6fb635537`, task `tsk_98ca0b059863f25a3b2b8d08` và root được phép `<PRIVATE_RESEARCH_ROOT>\_REMOTEMCP_MAINTENANCE`. Agent bảo trì `<DEDICATED_MAINTENANCE_AGENT_ID>` là đối tượng đã đăng ký, **không có lease hiện hành**. Không coi điều này là một shell hay một lối tắt bảo mật.

- Thêm **một API command loại `MAINTENANCE_OPERATION`** (đề xuất, CHƯA TRIỂN KHAI); receiver so khớp `device_id`, `project_id`, root, `approved_operation_id`, `script_sha256`, `capability_id`, `expiry`, nonce chống phát lại, đúng signing route generation và thế hệ lease task; mặc định DENY.
- Registry chỉ có thao tác được đóng băng từ trước (`read_only_status`, `read_only_receipts`, `gateway_preflight`, `gateway_promote` có phê duyệt riêng và an toàn node, `admission_audit`), mỗi thao tác mapping đến script *và* argv profile cố định; không nhận arbitrary `argv`, `-Command`, `-EncodedCommand`, `Invoke-Expression`, wildcards hay symlink/junction escape.
- Tách quyền xem khỏi quyền thay đổi: tác vụ đọc chỉ read DB `mode=ro`, không nhận `job_submit`; lệnh chuyển phiên bản có consent/approved change id, `zero_current_jobs`, no unexpired commands, code SHA, ghi evidence và operator fallback. Node remote lane **không được chạy gateway updater hay reboot node** trừ khi lối bảo trì privileged riêng được chứng minh không tạo deadlock tự cập nhật.
- Cấp quyền riêng ở node hoặc gateway maintenance broker có giới hạn danh tính, đường dẫn và allowlist script; không thêm `powershell` vào `MCP_DURABLE_ALLOWED_CMDS` chung. Nếu môi trường không cho privileged maintenance broker, chỉ cho phép operator local chạy exact pinned `-File` và đánh dấu `REMOTE_MAINTENANCE_EXECUTION=UNAVAILABLE`.
- Không dùng `task_job_submit` với lệnh `python -c` để tránh kiểm tra shell, không dùng task CQG làm phương tiện; không chuyển pairing keys/lease tokens vào argv, log hoặc repo.
- Chứng minh một test cấp quyền: approved op thực thi; cùng token nhưng sai script hash, ngoài root, hết hạn, lệch device/route, sai operation, lệnh tùy ý đều **DENIED**; không tạo job khoa học. Quyết định cho `gateway_promote` và đổi quyền dispatch là một phê chuẩn khác.

## 5. Bộ kiểm thử hồi quy đã cố định (được triển khai và chạy ở gate sau)

Phải chạy trên **Windows 11 / Windows CI** và **Linux CI**, Python 3.13 theo ma trận. Tránh phụ thuộc live credentials bằng node/gateway local loopback và SQLite synthetic fixture; với live testing không bao giờ giả lập là thực địa.

1. **T-POLL-01 — nhịp rỗng:** 60 poll cycles ở mỗi OS, gateway poll tối đa 2 giây và trả `204`; không có 502 do chờ rỗng; node không bị starvation bởi heartbeat đến kỳ; ghi `poll_gap_ms`.
2. **T-POLL-02 — lỗi heartbeat:** inject `heartbeat` timeout/HTTP 502, kiểm tra log phase chính xác và bounded backoff; không coi thất bại heartbeat là `jobs=0`.
3. **T-POLL-03 — lỗi SQLite:** giả lập `SQLITE_BUSY/SQLITE_LOCKED/locking protocol` riêng; fail closed; worker/command state giữ nguyên; không replay công việc.
4. **T-POLL-04 — poll/gateway deadline:** inject HTTP 502 và proxy idle timeout, client hồi phục ký poll mới, không thay identity, generation, nonce hoặc journal.
5. **T-POLL-05 — command lifecycle:** 6 lệnh đọc liên tiếp qua 2 task fixture; với từng lệnh assert `SUCCEEDED`, `delivery_attempt>=1`, node/gateway `request_hash` và route match, `client_elapsed_ms<55,000`; không có duplicate execute.
6. **T-POLL-06 — pressure/backpressure:** hàng đợi 19 `LEASED` hết hạn + nhiều `QUEUED` hết hạn không gây replay hoặc che khuất lệnh mới hợp lệ; giao lệnh đảm bảo fairness và timeout không đổi.
7. **T-LEASE-01…07:** mỗi class trong §3 có synthetic SQLite fixture trên Windows/Linux, kiểm tra tổng cardinality, lease/command expiry độc lập, `INVENTORY_DRIFT`, mismatch fail-closed, no writes.
8. **T-MAINT-01…08:** tất cả tiêu chí capability ở §4; mọi trường hợp deny phải không chạy process. Đặc biệt `powershell.exe` qua `JOB_SUBMIT` phải vẫn bị `COMMAND_NOT_ALLOWED` theo chính sách toàn cục.
9. **T-REGRESSION:** chạy `tests/v2bd/test_bounded_signed_poll_no_starvation.py`, `tests/v2bd/test_node_reconnect.py`, hai-node isolation, pinned dispatch hold, cùng test lịch sử được GH Actions đang chạy. Độc lập reviewer xác minh source hash trước khi chấp nhận.

**Gate đo đạc:** `EVENT_SCHEMA_PASS`; `NO_MUTATION_SAFETY_PASS`; `WINDOWS_TEST_PASS`; `LINUX_TEST_PASS`; `LEASE_19_CLASSIFIED_PASS` (chỉ khi đã chạy read-only trên snapshot thật); `SIGNED_RECEIPTS_PASS`; `REMOTE_MAINTENANCE_POLICY_PASS`. Các gate này chưa được đánh dấu PASS bởi tài liệu thiết kế.

## 6. Phép thử sản xuất sau khi triển khai và mở quyền

Chỉ tiến hành sau static QA Windows/Linux PASS, release pin mới hợp lệ, chủ vận hành phê duyệt cập nhật node nếu thật sự cần, số job thật và chưa đối soát đều 0 và *không có unexpired commands*. Trước mọi thay đổi phải lưu bằng chứng snapshot, cùng release marker; không restart khi còn công việc.

Cho phép **một lượt kiểm thử quản lý tuần tự** trên đúng các task đọc đã chọn, ít nhất **6 `TASK_LIST_DIR` thành công liên tiếp**. Với mỗi lượt ghi `start/client_elapsed_ms/command_id/gateway_created_ms/node_received_ms/node_finished_ms/gateway_finished_ms/receipt_hash/route_generation`; mỗi lượt **<55 giây**, node/gateway `SUCCEEDED`, không hiện `DEVICE_COMMAND_PENDING`, không có duplicate. Nếu lượt đầu FAIL, **STOP ngay** (không thử tiếp 5 lần gây backlog). Sau đó đối chiếu signed attestation node/gateway, online/fresh, pin và admission.

Nếu bất kỳ điều kiện nào FAIL => `REMOTE_MANAGED_ROUTE=HOLD`, `LIVE_6X=FAIL`, **không thông báo agent CQG-RU/J4.2 tiếp tục**. CI localhost không thay thế live 6x. Không mở research TEST hay TRAIN.

## 7. Cổng chuyển pha và tuyên bố cố định

P (tài liệu này, khóa giả thuyết/thước đo) → R (triển khai instrumentation, classifier, policy với QA độc lập) → E (read-only snapshot + đo đạc zero-science) → A (independent adjudication) → M (chỉ sau evidence chỉ rõ mechanism) → C (6x live production recovery qualification sau phê duyệt).

Tại thời điểm P:
`GATEWAY_PROMOTION=PASS`; `NODE_PIN=PASS`; `SIX_CONSECUTIVE_LIVE_MANAGED_READS=NOT_PASS`; `REMOTE_MAINTENANCE_CHANNEL=NOT_PASS`; `LEASE_19_ITEMIZED=NOT_YET_EXECUTED`; `SCIENTIFIC_JOBS=UNTOUCHED`; `SCIENCE_AGENTS_RESUME=FORBIDDEN`.

**Bước tiếp theo duy nhất sau P:** `REMOTEMCP_V31_NODE_POLL_CADENCE_INSTRUMENTATION_AND_19_LEASE_READONLY_CLASSIFIER_IMPLEMENTATION_STATIC_PREFLIGHT`. Đây là triển khai/QA hạ tầng, không phải mở nghiên cứu khoa học.
