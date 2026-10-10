# THƯ BÀN GIAO — RemoteMCP V3.1: bốn lệnh hết hạn, đo nhịp node, kiểm định phát hành

**Ngày:** 10/10/2026  
**Mục tiêu phiên sau:** tiếp tục quản trị phục hồi RemoteMCP theo đúng cổng, **không** nhầm kiểm thử trên nhánh với quyền triển khai sản xuất; trả lời rõ điều kiện để các agent CQG-RU, CQG-J4.2, CFAR và IRIS hoạt động trở lại.  
**Phạm vi được phép:** hạ tầng RemoteMCP, kiểm định độc lập, mã nguồn và báo cáo vận hành. **Không** thực hiện công việc khoa học CQG, SIX, CFAR, IRIS trong cùng gate này.

> **Điểm dừng bảo đảm:** dữ liệu cuối cùng được kiểm tra từ GitHub và kết nối quản lý ở 10/10/2026. Không suy ra trạng thái hiện tại của node hoặc quyền nghiên cứu trong cuộc hội thoại mới mà không kiểm tra lại.

## 1. Quy tắc quản trị bắt buộc

1. **Tiếng Việt hoàn toàn** khi báo cáo; thuật ngữ tiếng Anh kỹ thuật chỉ đặt trong ngoặc sau thuật ngữ tiếng Việt khi cần.
2. Với dự án khoa học, thực hiện tiền đăng ký → triển khai → thực thi → kiểm định độc lập → phân xử. Sử dụng đúng các chế độ **Khám phá / Đo lường / Xác nhận / Phân loại sự cố** (E/M/C/T), không vượt quyền gate.
3. **\`Lineage.md\` chỉ bổ sung cuối (append-only)** và chỉ ghi kết quả **khoa học** đã được phân xử, kể cả **PASS lẫn FAIL**. Không ghi sửa mã, kiểm thử hạ tầng, khôi phục node, chuyển nhánh Git, tạo job hoặc ghi nhật ký kỹ thuật vào \`Lineage.md\`.
4. Không phát lại lệnh lịch sử, không tạo job thử nghiệm bằng task khoa học, không claim task đang \`RECOVERABLE\` nếu chưa kiểm tra namespace/owner/barrier, không dọn \`cleanup_pending\` tùy tiện, không chạy CAL/TEST mới.
5. Giữ \`machine-1\` / node / watchdog / VBS / Startup / cặp khóa thiết bị / định danh tuyến hiện hành. **Cấm cập nhật hoặc khởi động lại node/gateway sản xuất nếu chưa có cổng đủ điều kiện và phê duyệt vận hành riêng.**
6. Không thêm \`powershell.exe\`, \`cmd.exe\` hoặc shell tùy ý vào danh sách chương trình được phép (allowlist) toàn cục. Đường bảo trì có quyền giới hạn (scoped maintenance capability) **chưa PASS**.
7. Mỗi lượt báo cáo cho người dùng phải kết thúc bằng **“Bước thực hiện tiếp theo”** với đúng một gate hợp lệ.
8. GitHub là nguồn sự thật của mã nguồn; số liệu thực địa cần bằng chứng riêng, đối chiếu hai phía. Một commit/CI PASS không đồng nghĩa quy trình trên máy thực PASS.
9. Không đưa tệp trạng thái, bản chụp các lệnh riêng tư, khóa, mã định danh thiết bị/agent/command hoặc đường dẫn tài khoản cá nhân lên GitHub công khai. Phục hồi những ID này từ nguồn quản lý đã kết nối và bản chụp riêng tư, không phỏng đoán từ tên.

## 2. Dòng lịch sử hạ tầng đã xác thực

- Bản gateway **cũ**: \`9f10cec250a9a266daa073ebd0df31ea16727f39\`.
- Bản gateway **sản xuất đã nâng thành công**: \`ef9f81f05ea2be9116751280340c47bd05d908ad\`; script nâng tại local trước đó đã hoàn tất với \`REMOTEMCP_BOUNDED_POLL_GATEWAY_PROMOTION_LOCAL=PASS\`. Bản nguồn cũ được giữ làm đối chứng, trạng thái runtime được tái sử dụng.
- Bản **node sản xuất** giữ nguyên: \`dabce9a76502dbae541a0eddff0096fb8dfe908a\`, thiết bị \`machine-1\`, thế hệ định tuyến \`1\`. Node **chưa nâng lên bản có instrumentation**.
- Trên hệ thống đã từng quan sát \`sqlite3.OperationalError: locking protocol\`, mã HTTP 502, heartbeat/ngắt kết nối không đều. **Chưa đủ cơ sở kết luận nguyên nhân duy nhất**, không tự khởi động lại tiến trình để “sửa”.
- Một lệnh đọc quản lý đã được node và gateway ghi kết thúc \`SUCCEEDED\`, nhưng node chỉ nhận sau khoảng **50,459 giây** và công cụ phía khách trả \`DEVICE_COMMAND_PENDING\` sau khoảng **62,6 giây**. Do đó **đường đọc quản lý sáu lần tuần tự (6/6) chưa PASS**; biên nhận hoàn tất muộn không thay thế ngân sách phản hồi phía khách (<55 giây/lượt).
- Một thử nghiệm kênh PowerShell qua tác vụ bảo trì bị node từ chối \`COMMAND_NOT_ALLOWED\`. Đó là chính sách lệnh bền vững (durable executor), **không phải chỉ do chậm mạng**. Không mở PowerShell toàn cục.
- Tại lần kiểm tra quản lý gần nhất trong phiên này: **\`machine-1 ONLINE\`, tín hiệu sức chứa còn mới, 0 job node đang chạy, 0 job node chưa đối soát, gateway \`ef9f81f\`, node \`dabce9a\`**. Cổng tiếp nhận mới có thể báo \`new_job_admission_allowed=true\`, **nhưng đây không phải chứng chỉ 6/6 hoặc quyền chạy thực nghiệm khoa học**. Node từng chuyển OFFLINE trong chính chuỗi xử lý: kiểm lại mỗi lần trước khi làm việc thực địa.

## 3. Bằng chứng bất biến: 19 lease, 4 lệnh chưa phân giải

- Có **19 lệnh lịch sử \`LEASED\` đã hết hạn**, không có lệch số lượng ở bản chụp; phân loại một-một: **15** có biên nhận node kết thúc (13 \`SUCCEEDED\`, 2 \`FAILED\`) nhưng gateway còn stale; **3** đã phát giao nhưng không có biên nhận node; **1** có node \`EXECUTING\` nhưng thiếu biên nhận kết thúc.
- Mã SHA-256 danh sách bản chụp **19 dòng**: \`9d316322454b5894a3093688d8a0b7090e221312b70153c6bf2ba038016fdd85\`.
- Mã SHA-256 kết quả **bốn lệnh** từ bộ điều tra chỉ đọc: \`99b041d1de02c63593992262133dca4a1f34c262536d684e0d3c66cf48e03d31\`.
- 4 trường hợp phải **giữ UNRESOLVED**:
  - \`JOB_SUBMIT\`: proxy vẫn \`QUEUED\`; không có biên nhận node, ánh xạ proxy tại node, thao tác/job tương ứng trong sổ bền vững, hoặc bằng chứng worker trong các bảng đã kiểm. Đây là **\`NO_PROVEN_EXECUTION_UNRESOLVED\`**, *không phải chứng minh “chưa bao giờ thực thi”*. Tuyệt đối không \`JOB_SUBMIT\` lại.
  - \`JOB_GET\`: không có biên nhận node; \`NO_PROVEN_EXECUTION_UNRESOLVED\`.
  - \`PROJECT_PROBE\`: không có biên nhận node; \`NO_PROVEN_EXECUTION_UNRESOLVED\`.
  - \`TASK_BASE_RESOLVE\`: node ghi \`EXECUTING\` không có thời điểm kết thúc; \`NODE_NONTERMINAL_STALE_UNRESOLVED\`.
- Bản chụp và đối soát có \`read_only=true\`, \`database_changes=0\`, \`replay_authorized=false\`. **Chất lượng kiểm định hồ sơ = PASS; hòa giải lịch sử = UNRESOLVED.**
- Không sửa một trong 19 hàng lịch sử, không xóa hàng stale, không tăng \`delivery_attempt\`, không đổi \`CANCELLED/LEASED\` sang \`QUEUED\`, không đánh dấu \`LOST/SUCCEEDED\` nếu thiếu chứng cứ.
- Bằng chứng JSON và định danh thật được giữ ở thư mục \`control\` riêng tư của \`machine-1\`; tên tệp nhận diện gồm \`v31-19-leases-20261010-143838\` và \`v31-four-commands-readonly-20261010-173904\`. Không đưa JSON thô lên repository công khai.

## 4. Cây công việc GitHub và các cổng đã đạt

**Kho mã:** \`https://github.com/minhtri22/remote-mcp\`.

| PR / vai trò | Trạng thái đủ bằng chứng |
| --- | --- |
| [PR #72](https://github.com/minhtri22/remote-mcp/pull/72) — đăng ký trước nhịp node và phân loại lease | Bộ hợp đồng và kiểm thử nền Windows/Linux PASS; vẫn là PR nháp |
| [PR #73](https://github.com/minhtri22/remote-mcp/pull/73) — mã đo từng pha + bộ điều tra chỉ đọc bốn lệnh | 14/14 kiểm thử trên SHA đã ghim PASS; không triển khai node; vẫn PR nháp |
| [PR #74](https://github.com/minhtri22/remote-mcp/pull/74) — tiền đăng ký cách ly bốn lệnh và chuẩn xét phát hành node | 2/2 kiểm thử đăng ký trước PASS; vẫn PR nháp |
| [PR #75](https://github.com/minhtri22/remote-mcp/pull/75) — triển khai cách ly không ghi đè, kiểm định độc lập | **8/8 CI PASS, 0 FAIL, 0 pending** ở commit \`6c946d1903b4d0b5b890b735f9439ea14b127105\`. PR **OPEN + DRAFT**, chưa merge/deploy |

**Chi tiết PR #75 — nguồn đã được kiểm:**
- \`remotemcp/routing/historical_isolation.py\`: bộ quyết định cách ly lệnh quá hạn; chỉ kích hoạt nếu có đủ manifest riêng tư, mã SHA-256 chính xác, khóa công khai và chữ ký Ed25519 hợp lệ; so sánh nguyên vẹn bản chụp đủ 19 lệnh, ràng buộc bốn loại \`JOB_SUBMIT/JOB_GET/PROJECT_PROBE/TASK_BASE_RESOLVE\`, operation/step/hash/route/proxy. Không nhận shell không kiểm soát.
- \`remotemcp/routing/commands.py\`: mặc định \`self.isolation=None\`; có bảo vệ trước tạo/hồi sinh lệnh, bảo vệ alias của operation/proxy, lọc đúng bốn lệnh khỏi poll/requeue/lease, chặn biên nhận đến muộn có thể ghi lại dòng lịch sử bảo vệ. **Không kích hoạt trong production**.
- \`tests/v31/test_v31_expired_four_isolation.py\`: tình huống \`ISO-01...ISO-12\` và các trường hợp tham số bổ sung, bao gồm chữ ký, sai lệch định danh, thao tác trùng, lỗi SQLite, hậu quả rollback và đối chứng.
- \`tests/v31/test_v31_isolation_independent_qa.py\`: bộ so sánh SQL độc lập trước/sau, kiểm chứng không sửa dòng bảo vệ.
- \`.github/workflows/v31-protected-four-isolation-qa.yml\`: kiểm thử trên Windows/Linux.
- Lịch sử CI ban đầu có **FAIL do lỗi cú pháp newline trong code tạo ra**, rồi **FAIL ở fixture SQLite thiếu COMMIT**. Đã sửa đúng nguyên nhân, không giảm gate; commit **cuối cùng ở trên** đạt 8/8 PASS. Không lấy lỗi đã khắc phục để kết luận cơ chế khoa học thất bại; cũng không che giấu lịch sử FAIL trong báo cáo kỹ thuật.
- PR #75 **chưa được merge**. Việc CI PASS là **\`STATIC_QA_PASS\`**, **không** chứng minh \`PRODUCTION_ISOLATION_PASS\`, \`NODE_PROMOTION_PASS\` hoặc \`LIVE_6X_PASS\`.

**Quan trọng:** Thư bàn giao được commit trên nhánh \`handoff/remotemcp-v31-isolation-20261010\` tách từ commit PR #75 đã đạt 8/8, để **không làm thay đổi SHA đã kiểm định** hoặc tự tạo vòng CI mới cho PR #75. Không coi commit tài liệu này là bằng chứng bộ mã mới đã được CI kiểm định.

## 5. Hiện trạng và quyền của các agent liên quan

**Kiểm tra quản lý gần nhất:** \`machine-1 ONLINE\`, gateway/node vẫn ở bản cũ đã ghim tương ứng, sức chứa node còn mới, không có job vật lý đang chạy. Tất cả task dưới đây cần tìm **đúng định danh đã có** thông qua nguồn RemoteMCP riêng tư; **không tạo task thay thế chỉ vì bị RECOVERABLE**.

| Nhánh | Quan sát gần nhất | Quyền hợp lệ hiện tại |
| --- | --- | --- |
| CQG-RU | Task \`RECOVERABLE\`, owner null, cleanup pending false | Không mở lại job khoa học; chỉ đọc/đối soát và chuẩn bị tài liệu bên ngoài tuyến chưa xác minh |
| CQG-J4.2 | Có **hai** task \`RECOVERABLE\`, owner null, **cleanup_pending=true** | **Stage G vẫn \`namespace_pristine=UNRESOLVED\`**; cấm CAL/TEST mới, không tự dọn hoặc ghi đè worktree |
| CFAR | Task \`RECOVERABLE\`, owner null, cleanup pending false | Chưa đóng P0 local; cần đối chiếu Git origin, checkout sạch, ghim nguồn. Chỉ sau đó mới xét khóa hợp đồng E1, chưa chạy exploratory one-shot |
| IRIS | Chưa có kiểm tra task ID riêng trong lượt kết thúc này | Giữ nguyên gate tiền thực thi và khóa, không khẳng định IRIS ONLINE/READY hoặc mở TRAIN/TEST |
| Bảo trì RemoteMCP | Task quản trị \`RECOVERABLE\`; thử \`JOB_SUBMIT\` PowerShell cũ bị \`COMMAND_NOT_ALLOWED\` | Có thể xử lý mã GitHub, kiểm định tĩnh và đọc quản lý; **kênh thực thi PowerShell bảo trì riêng chưa PASS** |

**Ý nghĩa:** Cổng kỹ thuật có thể tiếp nhận command là điều kiện **cần nhưng không đủ**. Chỉ được mở agent khoa học khi gate định tuyến có biên nhận tuần tự 6/6, hàng việc vật lý an toàn, quyền task/worktree nguyên vẹn và gate khoa học riêng đã PASS. Các agent có thể làm tài liệu/đăng ký trước độc lập trên GitHub ở môi trường không sinh lệnh node, nhưng **không được giao job thực nghiệm, claim/restart tùy tiện hoặc thay CAL/TEST**.

Theo ảnh người dùng ở hội thoại này:
- CQG-J4.2: gate \`CQG_J4_2_STAGE_G_EXTERNAL_ARCHIVE_PRISTINE_AUDIT_COVERAGE_RECOVERY\`, cần kiểm toán \`_tmp\`, \`runs\`, \`results\`, worktrees cũ, ZIP/JSON/binary và kiểm định độc lập toàn bộ vùng hạt giống. Đến khi bằng chứng đủ: \`Stage G = UNRESOLVED\`; fresh CAL/TEST **FORBIDDEN**.
- CFAR: E1 mới có tài liệu ban đầu; chưa khóa hợp đồng khoa học E1, chưa mở thực nghiệm; cần hoàn tất P0 local Git reconciliation trước. Không tự coi dữ liệu trong ảnh là đã được kiểm tra trực tiếp trên máy ở lượt này.

## 6. Điều kiện chặn quyết định (hard stop)

Dù kiểm định tĩnh PR #75 **PASS**, các điều kiện sau vẫn **chưa thỏa**:

1. Manifest riêng tư được ký và cố định theo đúng tập bốn command thực, kiểm tra độc lập, không làm biến đổi bảng lịch sử.
2. Cổng xét phát hành node đo nhịp riêng: khóa mã nguồn/artefact, kiểm thử Windows/Linux, đo chi phí ghi vết, hai kiểm tra an toàn node liên tiếp có chữ ký cách ít nhất 30 giây, không có lệnh còn hạn, bảo toàn singleton/PID/khóa/thế hệ định tuyến.
3. **Phê duyệt riêng của người vận hành trước khi nâng node/gateway** hoặc đổi chính sách phát lệnh; chưa có phê duyệt từ yêu cầu “commit handoff”.
4. Đường quản lý sản xuất: **6 lượt đọc có biên nhận hoàn tất tuần tự**, mỗi lượt phía khách **dưới 55 giây**, gateway/node kết thúc \`SUCCEEDED\`, cùng route/hash và không phát lệnh trùng. Dừng ngay nếu lần đầu FAIL.
5. Kênh bảo trì qua RemoteMCP (scoped capability) được kiểm định riêng; không suy ra đã PASS từ việc người dùng tự chạy PowerShell cục bộ.
6. Bốn lệnh lịch sử vẫn chưa có bằng chứng terminal: **không** được nâng nhãn thành \`RECONCILED=PASS\`; chỉ một cổng phân xử độc lập riêng có thể xác nhận được kết quả mới.
7. Namespace/worktree và khóa khoa học của từng dự án phải PASS độc lập, không coi hạ tầng có khả năng trả lời status là quyền thực nghiệm.

## 7. Quy trình phiên kế tiếp

**Bước 1 — đọc, không làm biến đổi:** mở PR #75, commit đã CI-PASS, PR #74 prereg và thư này. Đối chiếu thiết bị \`machine-1\` qua công cụ quản lý: gateway/node release, heartbeat/capacity fresh, 0/0 job vật lý, trạng thái task đang \`RECOVERABLE\`, cleanup pending; ghi mọi sai lệch là \`HOLD\`. Không tạo task/node job mới.

**Bước 2 — kiểm định độc lập mã bảo vệ và khóa ứng viên phát hành:** đánh giá thêm mọi cửa tạo/tái sử dụng thao tác ngoài \`CommandRepository\` (đặc biệt đường job proxy), chứng minh không lọt lệnh bảo vệ qua \`operation_id/step\`, late ACK, idempotency. Kiểm tra chữ ký, sai lệch manifest, hoạt động mặc định tắt, lỗi SQLite và chuyển trạng thái. Nếu tìm được lỗ hổng, bổ sung fixture rồi chạy QA 2 OS lại; **không merge** trước khi độc lập PASS.

**Bước 3 — bộ bằng chứng xét phát hành node:** tiếp tục bằng gate nêu dưới đây, là **công việc thiết kế/đánh giá**, **không phải lệnh triển khai**. Kiểm tra baseline/candidate release hash, ngân sách tài nguyên, cách tạo/rollback artefact, cổng pin và cửa sổ vận hành. Cần hỏi phê duyệt người vận hành trước khi thay đổi tiến trình thật.

**Bước 4 — chỉ sau đầy đủ proof:** cân nhắc mở kế hoạch ứng dụng lớp bảo vệ lên gateway, sau đó mới xét node telemetry release theo cửa riêng. Mọi bước phải fail-closed và giữ công việc khoa học ở trạng thái HOLD trong khi kiểm chứng.

**Không được**: thử xem đã sửa đường giao lệnh chưa bằng \`JOB_SUBMIT\` tới task CQG, tự restart node khi máy đang có việc, tự submit lại lệnh \`JOB_SUBMIT\` lịch sử, tự xóa 19 lease, tự clear cleanup pending, tự ghi kết quả science \`Lineage.md\`, hoặc thông báo các agent đã READY khi chưa có live 6x.

## 8. Cổng hợp lệ tiếp theo — đề xuất duy nhất

\`REMOTEMCP_V31_NODE_CADENCE_RELEASE_QUALIFICATION_EVIDENCE_PACK_AND_OPERATOR_AUTHORIZATION_GATE\`

Mục tiêu:
- Ghim PR #75 static QA 8/8 và soát độc lập mọi lối bypass trước candidate release.
- Tạo bộ bằng chứng phát hành, kế hoạch kiểm tra trước/sau, điều kiện pin/rollback và đo đạc; không có việc chuyển nguồn sản xuất trong gate tài liệu này.
- Thu bằng chứng hai ảnh chụp signed capacity mới theo thời gian khi đường đọc cho phép, song **không coi đây là phép thử 6x**.
- Nếu điều kiện thiếu hoặc agent không có quyền chạy lệnh thực tế: \`HOLD\`, không tự tìm cách lách giao thức.
- Chỉ đưa ra yêu cầu phê duyệt vận hành riêng; **không coi bản thân gate này là quyền nâng node**.

**Verdict hiện tại:** \`PR75_STATIC_QA=PASS\`; \`GATEWAY_UPGRADE=PASS\`; \`NODE_RELEASE=UNCHANGED\`; \`HISTORICAL_FOUR=UNRESOLVED\`; \`HISTORICAL_REPLAY=FORBIDDEN\`; \`LIVE_MANAGED_6X=NOT_PASS\`; \`REMOTE_MAINTENANCE_SHELL=NOT_PASS\`; \`SCIENCE_AGENTS_RESUME=FORBIDDEN\`.

---

### Lời nhắn cho agent nhận bàn giao

Giữ đúng phân biệt **đo đạc – kiểm định tĩnh – quyền sản xuất – kết quả khoa học**. Có thể hoàn tất hồ sơ và kiểm tra mã ngay trên GitHub mà không động tới job đang chạy. Không lấy trạng thái \`ONLINE\`, “0 job”, “new-job admission allowed” hoặc “8/8 CI PASS” để mở khoa học. Báo cáo tiếng Việt, thuật ngữ chuyên môn trong ngoặc, kết thúc mỗi lượt bằng đúng **bước thực hiện tiếp theo**.
