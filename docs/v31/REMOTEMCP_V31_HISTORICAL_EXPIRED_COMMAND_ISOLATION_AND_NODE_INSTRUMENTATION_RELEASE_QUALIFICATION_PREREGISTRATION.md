# REMOTEMCP_V31_HISTORICAL_EXPIRED_COMMAND_ISOLATION_AND_NODE_INSTRUMENTATION_RELEASE_QUALIFICATION_PREREGISTRATION

**Ngày:** 2026-10-10 · **Chế độ:** đăng ký trước (preregistration) · **Phạm vi:** hạ tầng RemoteMCP V3.1 · **Trạng thái:** PREREGISTRATION ONLY; NO EXECUTION LOCK.

**Bất biến quản trị:** không triển khai/khởi động lại node hay watchdog, không đổi nguồn gateway đang chạy, không mở pairing/secret, không phát lại/huỷ/dọn/sửa lệnh lịch sử, không thay đổi job hay dữ liệu CQG-RU/CQG-J4.2/IRIS. Không thêm PowerShell vào danh sách lệnh được phép toàn cục. Không báo các agent nghiên cứu tiếp tục khi chưa có cổng bằng chứng mới.

## P0. Ghim đối tượng và chứng cứ — không đưa định danh riêng tư vào GitHub

- Gateway sản xuất được ghim: \`ef9f81f05ea2be9116751280340c47bd05d908ad\`. Node hiện hành: \`dabce9a76502dbae541a0eddff0096fb8dfe908a\`; thế hệ định tuyến là \`1\` tại thời điểm bằng chứng.
- Bản chụp chỉ đọc 19 lệnh \`LEASED\` hết hạn: 15 lệnh có node terminal nhưng gateway stale; 3 lệnh đã phát giao, chưa có node receipt; 1 lệnh node \`EXECUTING\` chưa terminal. SHA-256 danh sách hàng cố định: \`9d316322454b5894a3093688d8a0b7090e221312b70153c6bf2ba038016fdd85\`.
- Bản đối soát chính xác bốn lệnh: \`remotemcp.v31.four-command-forensics.v1\`, SHA-256 kết quả \`99b041d1de02c63593992262133dca4a1f34c262536d684e0d3c66cf48e03d31\`. Báo cáo xác nhận \`read_only=true\`, \`database_changes=0\`, \`replay_authorized=false\`, bốn identity nhất quán, \`all_fully_reconciled=false\`.
- Định danh thực của bốn lệnh, device, project, task và đường dẫn evidence chỉ ở bản chụp **riêng tư trên máy vận hành**; mỗi lần dùng chính xác snapshot, kiểm lại digest/identity và số hàng. Không sao chép tệp riêng tư vào repository hoặc đầu ra kiểm thử công khai.
- Trường hợp \`JOB_SUBMIT\`: gateway proxy \`QUEUED\`, node mapping vắng, không tìm thấy thao tác/job tương ứng theo khoá \`v2bd-node-job:{device_id}:{command_id}\`, không có terminal event/worker fingerprint trong các bảng đã kiểm. Kết luận **NO_PROVEN_EXECUTION_UNRESOLVED**, *không* phải \`NEVER_EXECUTED\`.
- \`JOB_GET\`, \`PROJECT_PROBE\`: không có node receipt, **NO_PROVEN_EXECUTION_UNRESOLVED**. \`TASK_BASE_RESOLVE\`: node \`EXECUTING\`, không có terminal timestamp; **NODE_NONTERMINAL_STALE_UNRESOLVED**.
- Ở một lần kiểm tra chuẩn bị đăng ký trước, thiết bị \`OFFLINE\`, \`capacity_signal_fresh=false\`, quyền tiếp nhận bị khóa \`NODE_RELEASE_ATTESTATION_NOT_FRESH\`. Không diễn dịch báo cáo hoạt động 0 job trước đó thành sự bảo đảm 0 job *hiện tại* khi thông tin đã cũ.

**Cấm đổi nhãn:** \`FORENSIC_QA=PASS\` chỉ là chất lượng đối chiếu, không suy ra \`HISTORICAL_TERMINAL_RECONCILIATION=PASS\`. Bốn lệnh tiếp tục \`UNRESOLVED\`.

## P1. Câu hỏi kiểm định, giả thuyết và đối chứng

**Q-ISO — Cách ly một command hết hạn có thể hoàn toàn không đụng tới trạng thái không?**
- H-ISO1: từ bản chụp bất biến, một lớp lọc an toàn (read-only protected-ID filter) từ chối **mọi yêu cầu giao lại** chính command/operation tương ứng trước đoạn ghi trạng thái; bản ghi SQLite lịch sử không thay đổi.
- H-ISO2: chỉ lọc tại poll là chưa đủ vì có nhánh tạo lệnh khi khôi phục thao tác cũ. Tại gateway hiện hành, \`create_in_tx\` có nhánh \`CANCELLED + DEVICE_COMMAND_EXPIRED\` → ghi lại \`QUEUED\`; \`poll\` cũng thực hiện đổi trạng thái cho các lệnh đang chờ còn hạn. Phải khóa đường create/revive/deliver/lease/recovery, không chỉ endpoint poll.
- H-ISO3: chỉ coi \`LEASED\` đã quá \`lease_expires_at_ms\` là không đủ; phải xác minh \`command_expires_at_ms\`, \`route_generation\`, \`request_hash\`, receipt, trạng thái gateway và lịch sử \`operation_id + operation_step\`. Không được thay lệnh cũ bằng command ID mới để né chặn.
- H-ISO4: có thể ngăn phát lại command cũ mà vẫn cho phép một yêu cầu đọc **mới, đúng quyền, operation ID mới** đi qua; tuy nhiên chỉ được chứng minh bằng kiểm thử tổng hợp, không phát lệnh thật trong pha này.

**Q-NODE — Bản node có đo được nguyên nhân trễ mà không thay đổi ngữ nghĩa thực thi?**
- H-N1: phép đo chỉ bật theo cấu hình \`REMOTEMCP_NODE_CADENCE_TRACE=1\`, các pha \`capacity_duration_ms\`, \`process_safety_duration_ms\`, \`heartbeat_duration_ms\`, \`poll_http_duration_ms\`, \`execute_duration_ms\`, \`result_ack_duration_ms\`, \`poll_gap_monotonic_ms\`, \`reconnect_backoff_ms\` đo thời gian cục bộ đơn điệu; không thay đổi cách poll, lệnh, TTL, retry, admission và lease.
- H-N2: lỗi SQLite \`locking protocol\` phải được ghi nhãn riêng, không được biến thất bại đọc \`capacity_snapshot\` thành capacity = 0 hoặc thành cớ tự restart.
- H-N3: việc ghi JSONL tùy chọn có thể phát sinh overhead/I/O lỗi; lỗi ghi nhật ký phải không làm mất command, đứt phiên hay che lấp lỗi gốc. Không đủ điều kiện release nếu telemetry thiếu pha hoặc chứa secret.

**Đối chứng cố định:** baseline node \`dabce9a\`, instrumented candidate HEAD từ PR #73 tại thời điểm ghim \`1106f960dc2670234a9e4bba4ff3703c154eb30c\` (**không phải một release sản xuất**), gateway \`ef9f81f\`. Mọi thay đổi sau hash phải tạo bộ khóa/QA mới.

## P2. Cách ly an toàn bốn lệnh (đặc tả chính sách, chưa triển khai)

**Tập đích chính xác**: chỉ bốn command từ snapshot 19 hàng, chọn bằng \`classification\`:
\`EXPIRED_DELIVERED_NO_RECEIPT\` đúng 3 và \`EXPIRED_NODE_RECEIVED_NONTERMINAL\` đúng 1; bốn \`command_type\` là \`JOB_SUBMIT\`, \`JOB_GET\`, \`PROJECT_PROBE\`, \`TASK_BASE_RESOLVE\`. Kiểm \`sha256\` danh sách đủ 19 hàng, đúng một \`device_id\`, \`route_generation\` và \`request_hash\` trước khi tạo tập. Nếu số đếm/digest/identity lệch: \`ISOLATION_INPUT_INVALID_HOLD\`, không tiếp tục.

**Điều kiện bảo vệ** (lý luận một lần, không được gán lại trạng thái):
1. Nếu \`command_id\` thuộc tập cách ly → từ chối *readmission/re-lease/requeue/revive/recovery*; trả \`HISTORICAL_COMMAND_PROTECTED\` cho **yêu cầu tạo/tái giao mới**, không thay đổi chính dòng lịch sử.
2. Nếu yêu cầu có \`operation_id\` và \`operation_step\` trỏ đến lệnh được bảo vệ → từ chối cả khi thực hiện thao tác idempotency cũ. Nếu không xác lập được ánh xạ operation trước khi ghi, \`OPERATION_LINK_UNRESOLVED_HOLD\`.
3. Nếu gặp \`request_hash\`/generation/device liên quan cùng thao tác được bảo vệ nhưng command ID không khớp → \`AMBIGUOUS_REPLACEMENT_HOLD\`; không tự coi là lệnh mới, không tự cấp quyền cho \`JOB_SUBMIT\`.
4. Lệnh ngoài tập bảo vệ giữ nguyên luật quyền/bản phát hành/lease sẵn có. Không mở rộng phạm vi chặn sang các command lịch sử khác nếu chưa có khóa/bằng chứng riêng.
5. Không áp dụng thao tác \`UPDATE/DELETE\` lên *bất kỳ* trong 19 lệnh khi chạy classifier/quarantine qualification; không tác động bảng \`routed_jobs\`, \`node_commands\`, \`node_routed_jobs\`, \`jobs\`, \`operations\`. Nếu ứng dụng hiện tại vốn có maintenance transition thì chỉ chứng minh **bất biến bốn đích**, không tự động dọn các dòng khác.
6. Không sử dụng kết quả \`node_online=true\` để cấp phép giao lại. Khi \`OFFLINE\` hoặc attestation/capacity lỗi thời: \`FAIL_CLOSED\` toàn bộ việc thay đổi.

**Kiến trúc ưu tiên:** lớp đánh giá bảo vệ command chạy trước mọi DB mutation tại gateway và chỉ nhận **manifest riêng tư** được ký/digest, kiểm device/route/type/request/operation identity và không có nhánh cập nhật hàng lịch sử. Không triển khai quy tắc trên vào node, vì node cũ không có lớp bảo vệ này. Nếu không chứng minh được chặn *mọi* điểm gọi create/revive/poll/recovery, giữ \`ISOLATION_ENFORCEMENT=UNRESOLVED\`; không thử nghiệm bằng lệnh thực.

**Bất biến dữ liệu bắt buộc:** so sánh trước/sau byte SHA-256 của các bảng nguồn *ở fixture* và bản digest canonical của 19 dòng; trạng thái, \`delivery_attempt\`, \`lease_expires_at_ms\`, \`command_expires_at_ms\`, \`request_hash\`, \`operation_id\`, \`operation_step\`, \`node_receipt\` của từng lệnh không đổi; không có job/process mới. Không cấm một audit report độc lập được tạo kiểu create-only.

## P3. Ma trận kiểm định cách ly — thực hiện **ở pha sau**, không chạy production

| ID | Kích thích chỉ với fixture synthetic | Kết quả phải đạt |
| --- | --- | --- |
| ISO-01 | Bốn ID chuẩn, snapshot 19 hàng và digest khớp | Đúng 4 được bảo vệ, không ghi DB |
| ISO-02 | Cố tái poll cùng một \`LEASED\` hết cả command TTL lẫn lease TTL | Không giao, không đổi attempt/state |
| ISO-03 | Giả lập lệnh \`CANCELLED/DEVICE_COMMAND_EXPIRED\` với \`operation_id + operation_step\` trùng | Trả \`HISTORICAL_COMMAND_PROTECTED\`, không revive thành \`QUEUED\` |
| ISO-04 | Command mới, trùng payload nhưng operation mới và quyền đầy đủ | Không tự chặn bằng payload đơn lẻ; vẫn phải qua quyền bình thường |
| ISO-05 | Cùng operation nhưng command ID bị đổi hoặc thiếu liên hệ identity | \`AMBIGUOUS_REPLACEMENT_HOLD\` |
| ISO-06 | Hash, generation, device, type, count 19/4 hoặc thời hạn sai | \`ISOLATION_INPUT_INVALID_HOLD\`; không mutation |
| ISO-07 | Lệnh có node \`EXECUTING\` nhưng không terminal | Không giả terminal, cancel, replay hay đánh dấu LOST |
| ISO-08 | Lệnh \`JOB_SUBMIT\` thiếu durable job mapping | Không suy ra \`never executed\`, không submit lại |
| ISO-09 | Máy node OFFLINE, capacity/attestation không mới | \`FAIL_CLOSED\`; không phát lệnh |
| ISO-10 | Đối chứng 15 receipt terminal gateway-stale | Không sửa lịch sử, không bị chọn nhầm vào tập 4 |
| ISO-11 | Windows/Linux cùng fixture; chuyển vận hành gateway bảo vệ đích | Tương đương quyết định trên 2 hệ điều hành, không lệnh science |
| ISO-12 | Lặp lại thử crash/recover/DB lock ngay trước mọi đường create/revive | Không mất manifest bảo vệ hoặc giao lại lệnh hết hạn |

**Ngưỡng:** tất cả ISO-01…12 PASS trên Windows và Linux; 0 sửa hàng nguồn, 0 command lịch sử được giao lại, 0 job được tạo, 0 lỗi identity không bị chặn. Mọi kiểm thử ngoại lệ phải \`HOLD\`, không được biến lỗi fixture thành success.

## P4. Điều kiện đủ để xét cấp quyền phát hành node đo nhịp (node release qualification)

**Ranh giới pha:** \`PREREGISTRATION\` → triển khai lớp cách ly trên nhánh + independent static QA → chứng minh read-only snapshot và release lock trên môi trường biệt lập → chuẩn bị/cấp quyền vận hành riêng → *mới được xem xét* cập nhật node qua cửa sổ bảo trì phê duyệt. PR này **không cấp khóa thực thi**.

### R-QA: bắt buộc trên Windows và Linux
- Ghim source SHA candidate, artifact SHA-256, dependency lock, Python/runtime, bản release hiện tại và chữ ký/manifest trước/sau.
- Giữ nguyên đường node hiện hành và watchdog, không tự sửa Startup/VBS/venv; định danh device, fingerprint/route_generation unchanged.
- Tất cả 14/14 CI nền của PR #73 và 2/2 classifier của PR #72 phải PASS tại **đúng SHA cuối**, không dùng kết quả trên commit cũ. Kiểm thử ISO-01…12, đo phân đoạn, HTTP 502/timeout, SQLite \`locking protocol\`, no-secret, I/O-error nonfatal, signed poll/receipt, exactly-once, branch-serial, pinned admission, process safety và two-node isolation PASS.
- Chạy 60 vòng rỗng trên mỗi OS, không duplicate, pha poll có duration và mã status, error được phân loại chính xác; overhead telemetry **p95 ≤ 5 ms/vòng** trên fixture và không gây thay đổi biên nhận; telemetry disabled giữ cùng verdict/giao nhận như baseline.
- Ghim schema JSONL, số sự kiện, mã băm tệp, chi phí ghi; các log có \`command_id\` thôi, không \`argv\`, token, secret, stdout/stderr. Kiểm thử \`UNWRITABLE_TRACE_DIR\` không thay đổi dispatch/lifecycle.
- Đo latency giữa các event trong cùng process bằng monotonic clock; thời gian giữa node/gateway chỉ dùng wall time sau khi đánh giá lệch đồng hồ. Không tuyên bố fix poll starvation nếu chỉ thêm instrumentation.

### R-LIVE-BEFORE: điều kiện vận hành phải được kiểm lại ngay trước cửa sổ
- Operator xác nhận **node ONLINE, signed attestation fresh**, không còn job vật lý đang chạy/chưa đối soát, process singleton, không có workload dài; hai snapshot liên tiếp cách nhau tối thiểu 30 giây, cả hai PASS. Nếu node OFFLINE/không fresh → HOLD, không restart.
- \`UNEXPIRED_COMMANDS=0\`; command mới sau snapshot phải được kiểm lại; bảo vệ 4 hàng thực, đối chiếu manifest signed. Bốn hàng *vẫn UNRESOLVED* là chấp nhận được cho **nghiên cứu cách ly**, nhưng **không** dùng làm lý do xóa hoặc replay.
- Gate pinned node hiện có yêu cầu exact SHA cũ: nâng node phải kèm kế hoạch cập nhật chính sách pin theo đúng phiên bản mới, staging \`dispatch_hold\` và signed attestation sau cập nhật; không chủ động nới pin hoặc mở admission trước bằng chứng.
- Gateway release \`ef9f81f\`, node release \`dabce9a\`, process PID/parent tree, khoá public và state đã ghim phải được đối chiếu; vận hành dùng script versioned, rollback kế hoạch đã kiểm trên bản mô phỏng, checkpoint + bất biến dữ liệu trước/sau.
- Có phê duyệt riêng của người vận hành cho nâng node và lịch downtime. Pha này không được lén coi câu “thực hiện prereg” là phê duyệt nâng node.
- Nếu cần isolate process/restart, chỉ cân nhắc khi đảm bảo không có run khoa học; không phá node/watchdog nếu chưa giải trình được nguồn chết/SQLite locking.

### R-LIVE-AFTER: tiêu chí chứng minh *sau* thay đổi được phê duyệt (không thuộc pha này)
- Signed node/gateway exact pin mới, định danh/fingerprint/generation không thay đổi, heartbeat và capacity fresh, 0 physical unresolved, không có protected command chuyển trạng thái hoặc tăng attempt, 0 replay.
- Thu tối thiểu 60 vòng telemetry thật với tệp bằng chứng, kiểm phân đoạn và checksum, không in secrets. Nếu thiếu log/phase do node lỗi → không PASS.
- Sáu lượt \`TASK_LIST_DIR\` **lần lượt, mỗi lượt <55.000 ms**, mỗi lượt node và gateway terminal \`SUCCEEDED\`, cùng request hash/route/command ID, delivery attempt đúng và người gọi nhận đáp án trong ngân sách. **STOP ở lần FAIL đầu**; không retry bừa, không dùng chứng cứ về sau thay thế thời hạn client.
- Đo jitter/latency và so với baseline, riêng vấn đề SQLite/network phải có đối chiếu đủ pha; nếu vẫn fail, ghi \`ROUTE_RECOVERY=FAIL\` dù node đo đạc đã triển khai.
- Kênh bảo trì riêng (scoped capability) phải có qualification độc lập; không suy ra PASS từ node upgrade, không mở \`powershell.exe\` toàn cục.
- Chỉ khi **LIVE_6X_PASS + RELEASE_PIN_PASS + EXPIRED_ISOLATION_PASS + PHYSICAL_JOB_BARRIER_PASS + SCIENCE_GOVERNANCE_AUTHORIZATION_PASS** mới cân nhắc thông báo cho CQG-RU/CQG-J4.2/IRIS. Trạng thái của các agent là một gate riêng.

### Phân loại kết quả và quy tắc dừng

- \`PREREG_LOCKED\`: tài liệu nguồn/phép đo/đối chứng được ghim và được peer reviewer chấp thuận.
- \`ISOLATION_STATIC_PASS\`: fixture chứng minh an toàn, **chưa phải hiệu lực production**.
- \`NODE_RELEASE_QUALIFIED\`: kiểm định độc lập và bằng chứng đủ để **đề xuất** operator release authorization, không đồng nghĩa đã deploy.
- \`PRODUCTION_ISOLATION_PASS\`, \`NODE_PROMOTION_PASS\`, \`LIVE_6X_PASS\`: chỉ kết luận khi có biên nhận signed thực địa, không dùng CI hay source commit thay thế.
- Bất kỳ gate thiếu bằng chứng: \`HOLD/UNRESOLVED\`; không phát hành khóa, không bật maintenance shell, không replay, không thông báo science agents.
- Tuyệt đối không ghi các sự kiện sửa code, vận hành MCP, chạy CI vào science \`Lineage.md\`; chỉ ghi ở docs/CI/evidence hạ tầng.

## P5. Chuỗi công việc kế tiếp đã đăng ký

1. **Bước tiếp theo hợp lệ:** \`REMOTEMCP_V31_EXPIRED_FOUR_COMMAND_NONMUTATING_ISOLATION_IMPLEMENTATION_STATIC_PREFLIGHT_AND_INDEPENDENT_QA\`. Triển khai gateway protection (trước create/revive/poll/recover), thử Windows/Linux synthetic và đóng băng release hashes. Không production deploy.
2. Khi ISO độc lập PASS: \`REMOTEMCP_V31_NODE_CADENCE_RELEASE_QUALIFICATION_EVIDENCE_PACK_AND_OPERATOR_AUTHORIZATION_GATE\`. Cần đủ signed/physical safety proof và phê duyệt riêng; không tự nâng node.
3. Sau nâng có phê duyệt riêng: \`REMOTEMCP_V31_NODE_CADENCE_DEPLOYMENT_AND_LIVE_6X_SIGNED_RECEIPT_ADJUDICATION\`, STOP ở lỗi đầu, no replay.
4. Riêng bốn lệnh terminal reconciliation vẫn \`UNRESOLVED\` cho đến khi có nghiên cứu chuyên biệt chứng minh tương ứng. Không cố ép nhãn PASS để mở nhánh khác.

**Tuyên bố khóa của pha này:** \`PREREGISTRATION_ONLY\`; \`PRODUCTION_GATEWAY_PROMOTION=PASS\`; \`PRODUCTION_NODE_UNCHANGED\`; \`HISTORICAL_FOUR_TERMINAL_RECONCILIATION=UNRESOLVED\`; \`NONMUTATING_ISOLATION_IMPLEMENTED=NO\`; \`NODE_UPGRADE_AUTHORIZED=NO\`; \`LIVE_MANAGED_6X=NOT_PASS\`; \`SCIENTIFIC_AGENTS_RESUME=FORBIDDEN\`.
