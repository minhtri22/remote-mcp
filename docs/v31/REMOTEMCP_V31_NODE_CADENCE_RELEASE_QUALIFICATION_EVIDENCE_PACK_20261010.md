# REMOTEMCP V3.1 — Hồ sơ bằng chứng xét phát hành nhịp node và cổng phê duyệt vận hành

**Ngày:** 2026-10-10  
**Gate:** `REMOTEMCP_V31_NODE_CADENCE_RELEASE_QUALIFICATION_EVIDENCE_PACK_AND_OPERATOR_AUTHORIZATION_GATE`  
**Trạng thái:** `HOLD` — chỉ chuẩn bị bằng chứng/kiểm định nhánh; **không cấp quyền triển khai sản xuất**.

## 1. Các nguồn ghim và phạm vi

- Handoff: `70fbadeff7a37a285bc2a9f9f0a714f9d32b5ca4`, `docs/handoff/REMOTEMCP_V31_HANDOFF_2026-10-10.md`.
- PR #74 tiền đăng ký: `a7c4caa18820d1d3c704235f4b49326d72d5a48c`.
- PR #75 kiểm định cách ly tĩnh (gốc của nhánh này): `6c946d1903b4d0b5b890b735f9439ea14b127105`; Windows/Linux CI cũ PASS, **chỉ áp dụng cho SHA đó**.
- Gateway thực địa tại lần đọc quản lý: `ef9f81f05ea2be9116751280340c47bd05d908ad`.
- Node thực địa tại lần đọc quản lý: `dabce9a76502dbae541a0eddff0096fb8dfe908a` (không thay đổi).
- Ứng viên phép đo: `1106f960dc2670234a9e4bba4ff3703c154eb30c`; **chưa phát hành**, chưa ghim tệp nhị phân/khóa phụ thuộc của ứng viên cuối.
- Danh sách chứng cứ 19 lệnh: SHA-256 `9d316322454b5894a3093688d8a0b7090e221312b70153c6bf2ba038016fdd85`. Tập bốn lệnh: SHA-256 `99b041d1de02c63593992262133dca4a1f34c262536d684e0d3c66cf48e03d31`. Định danh thực và JSON thô chỉ lưu ở nguồn riêng tư.

**Phân tách trách nhiệm:** bộ hồ sơ này không thuộc nghiên cứu khoa học, không ghi `Lineage.md` và không mở CQG/SIX/CFAR/IRIS. Không thực hiện bất kỳ chuyển trạng thái của bốn/19 lệnh hoặc tác vụ khoa học nào.

## 2. Kiểm toán đường vòng phát hiện sau PR #75

**Phát hiện có thể kiểm chứng từ mã nguồn:** quy tắc `ExpiredFourIsolation.before_create` ở SHA PR #75 chỉ cấm proxy đã bảo vệ với `JOB_SUBMIT` và `JOB_RECOVER_ROUTED_JOB`. Trong khi đó gateway còn phát `JOB_GET`, `JOB_RESULT`, `JOB_CANCEL` trên cùng `proxy_job_id`. `JOB_CANCEL` có thể thay đổi trạng thái tác vụ phía node; `task_job_result` và các đường đọc có thể cập nhật `routed_jobs`. `RoutedJobRepository.update` ở SHA #75 không có bảo vệ tập proxy bất định, và `RoutedJobRepository.create` có nhánh tái sử dụng bằng `operation_id`/`execution_key` trước khi tạo routed command.

**Tác động:** kiểm định 8/8 ở PR #75 **không đủ bằng chứng để cho phép triển khai cách ly** nếu mọi đường chạm tới proxy được bảo vệ chưa bị xét. Đây là lỗ hổng phạm vi kiểm thử/đảm bảo trên nhánh ứng viên, **không phải bằng chứng bốn lệnh lịch sử đã bị chạy lại trên sản xuất**.

**Khắc phục trên nhánh kiểm định tách biệt:** chặn mọi lệnh có `proxy_job_id` thuộc tập bảo vệ tại `before_create`; gắn lớp bảo vệ đã xác thực vào `RoutedJobRepository` để từ chối `update`, `create` trùng operation và tái sử dụng qua execution key; bổ sung ISO-08 / ISO-08b và kiểm định cấu hình chèn chính sách. Không thay đổi `self.isolation=None` mặc định, không bật cấu hình bảo vệ, không merge PR #75, không triển khai sản xuất.

**Tình trạng sửa chữa:** bản sửa chỉ là ứng viên; yêu cầu kiểm thử độc lập Windows/Linux tại **SHA cuối** của nhánh này. Nếu có FAIL, giữ `HOLD` và sửa bằng bằng chứng, không giảm ngưỡng.

## 3. Đo đọc quản lý không biến đổi

Kết quả đọc tại ngày 10/10/2026 qua kết nối quản lý chính:
- `machine-1`: ONLINE; hai lần đọc trong phiên cho thấy tín hiệu sức chứa còn mới; 0 tác vụ vật lý đang chạy và 0 chưa đối soát tại thời điểm đọc; chứng thực node nguồn vẫn `dabce9a`, gateway vẫn `ef9f81f`.
- Đếm `active_commands=19` và `registry_nonterminal_routed_jobs=239` là **đếm registry/gateway**, không được diễn giải là 19 tác vụ vật lý hay 239 tác vụ đang chạy.
- Cần thu riêng hai ảnh chụp **có chữ ký kiểm được độc lập**, cách nhau ít nhất 30 giây, ngay trước cửa sổ thao tác thực; lần đọc quản lý ở phiên chat **không thay thế hồ sơ ký và pin riêng tư**.
- Không thực hiện lệnh kiểm tra tới node, không tạo/cancel/replay job, không cấp vỏ lệnh PowerShell.

## 4. Ma trận điều kiện bắt buộc

| Mã | Bằng chứng phải có | Trạng thái |
|---|---|---|
| ISO-STATIC | ISO-01..12 + ISO-08b và hồi quy trên Windows/Linux đúng SHA sửa chữa | HOLD — chờ CI độc lập trên SHA cuối |
| BYPASS-COVERAGE | Liệt kê mọi đường create/reuse/poll/ACK/proxy, đối chứng lệnh hủy và cập nhật registry | HOLD — mã sửa ứng viên, chưa adjudicate |
| HISTORICAL | 19 dòng bất biến, 4 vẫn UNRESOLVED, tuyệt đối 0 replay | Bảo toàn chính sách; xác minh thực địa trước triển khai |
| NODE-BASELINE | Gateway `ef9f81f`, node `dabce9a`, device/fingerprint/generation/singleton giữ nguyên | Đã quan sát release; các bằng chứng còn lại chưa đủ |
| RELEASE-ARTIFACT | SHA nguồn ứng viên, gói phát hành, khóa thư viện, hash artifact, đối chiếu bản cũ | HOLD |
| CADENCE-OS | >=60 poll rỗng trên mỗi OS, không thay đổi command lifecycle, đủ các pha | HOLD |
| CADENCE-COST | p95 phụ phí ghi vết <=5 ms/vòng và kiểm lỗi đường JSONL không làm hỏng lệnh | HOLD |
| NODE-SAFETY | Hai signed/fresh snapshots >=30s, 0 vật lý chạy, 0 unresolved, 0 lệnh chưa hết hạn, process singleton, route pin | HOLD — phải kiểm lại ngay trước cửa sổ |
| OPERATOR | Phê duyệt vận hành **riêng** với downtime, tập lệnh xác thực, phương án hoàn nguyên | NOT AUTHORIZED |
| LIVE-6X | Sau khi nâng **được duyệt**: 6 lần `TASK_LIST_DIR` tuần tự, mỗi lần <55 s, terminal receipt 2 phía khớp | NOT RUN / NOT PASS |
| MAINTENANCE | Năng lực bảo trì có giới hạn riêng; không cấp global PowerShell/cmd | NOT PASS |
| SCIENCE-RESUME | Bảo vệ + release pin + physical barrier + live 6x + từng namespace/gate khoa học PASS | FORBIDDEN |

## 5. Tiến trình phát hành đề xuất, chỉ để operator xem xét

1. Đóng khóa SHA kiểm định sau khi Windows/Linux và kiểm định độc lập PASS. Xác minh công khai chỉ chứa source/fixture tổng hợp, không chứa manifest/ID riêng tư.
2. Dựng bản phát hành riêng trên môi trường không sản xuất; ghi nguồn, hash phụ thuộc, artifact, manifest ký, cơ chế vô hiệu mặc định và bản sao rollback. Chạy 60 vòng poll rỗng và phép đo phụ phí trước/sau với bản ghi không chứa nội dung lệnh.
3. Trước cửa sổ vận hành: thu 2 ảnh chụp có chữ ký độc lập cách >=30s, đếm số lệnh **còn hạn** từ nguồn thẩm quyền, chứng minh vật lý 0/0, kiểm singleton/PID/watchdog/route/fingerprint, đối chiếu 19 dòng và bốn định danh riêng tư. Nếu bất kỳ bước nào không giải trình được → STOP/HOLD.
4. Xuất trình bản kế hoạch nguyên tử: checkpoint SQLite an toàn, chuyển cấu hình nguồn/binary đồng bộ với exact node-release pin và dispatch hold, nguồn bản cũ còn sẵn, điều kiện rollback **không phá công việc đang chạy**. Không nới pin một chiều khi node chưa attested.
5. **Chỉ sau phê duyệt riêng của người vận hành:** thực hiện thay đổi trên cửa sổ bảo trì; đọc sau nâng bắt buộc giữ 19 dòng bất biến, route/fingerprint/singleton đúng, 0 job bất định. Nếu sai: khóa tiếp nhận, thu chứng cứ và thực hiện rollback theo đúng khóa đã kiểm; không tự restart lặp lại.
6. Sau các proof trên, chạy đúng 6 lần đọc quản lý tuần tự, dừng ngay lần FAIL đầu, so khớp command ID/request hash/route, thời gian client <55s. Thành công muộn không thay thế thời hạn.

## 6. Quyết định tại phiên này

`STATIC_QA_PR75=PASS`; `BYPASS_REMEDIATION=UNQUALIFIED_PENDING_CI`; `RELEASE_EVIDENCE_COMPLETE=NO`; `NODE_UPGRADE_AUTHORIZED=NO`; `PRODUCTION_ISOLATION=NOT_ACTIVE`; `HISTORICAL_FOUR=UNRESOLVED`; `LIVE_6X=NOT_PASS`; `SCIENCE_AGENTS_RESUME=FORBIDDEN`.

**Bước hợp lệ kế tiếp sau bộ hồ sơ:** hoàn tất kiểm định độc lập của ứng viên chặn đường proxy trên cả hai hệ điều hành, khóa SHA, hoàn thiện bài đo cadence và signed evidence; chỉ sau đó mới được trình xin phê duyệt vận hành riêng. Không triển khai node/gateway ở gate này.


---

## Phụ lục: kiểm định độc lập tiếp diễn ngày 10/10/2026

**Tính chất:** chỉ tạo công cụ và chứng cứ kiểm định trên PR #76 nháp; không triển khai bản ứng viên, không thay gateway/node, tiến trình Windows, bộ giám sát, nhật ký hay cơ sở dữ liệu sản xuất. Không thay `Lineage.md`.

### Chốt quan sát thực địa

- Mốc kiểm định đã PASS **9/9** là mã `349689524f7d903654285d5bcb68709202c9c019`. Bất cứ commit bổ sung nào sau mốc này **phải kiểm lại CI tại đúng HEAD mới**; không kế thừa kết quả 9/9 một cách mặc định.
- Ảnh chụp chỉ đọc ngoài băng lúc khoảng **14:42 UTC ngày 10/10/2026**: 19 lệnh gateway còn trạng thái thuê đều đã hết hạn, 0 lệnh chưa hết hạn, không cắt cụt danh sách; công việc vật lý trên node 0 đang chạy và 0 chưa đối soát. Đây là **quan sát tại một thời điểm**, không phải khóa kỹ thuật duy trì số 0.
- Đối chiếu hai nguồn độc lập theo thời điểm: hồ sơ `v31-19-leases-20261010-143838-8ed63c72.json` và `gateway-pending-ledger-crossproof.json`: 19/19 định danh trùng nhau, không sinh/mất định danh, không lệch các trường chung `command_type`, `route_generation`, `gateway_state`, `delivery_attempt`, `task_id`, `project_id`. Hồ sơ bốn lệnh ghim cùng SHA danh mục `9d316322454b5894a3093688d8a0b7090e221312b70153c6bf2ba038016fdd85`; hồ sơ bốn lệnh có mã băm kết quả `99b041d1de02c63593992262133dca4a1f34c262536d684e0d3c66cf48e03d31`. Phép so sánh **chỉ đạt tương hợp cấu trúc một phần**, vì nguồn mới không có đủ `request_hash`, `command_expires_at_ms` để tái dựng chứng cứ toàn hàng bất biến.
- Bốn lệnh được bảo vệ vẫn không phân xử: `JOB_SUBMIT`, `JOB_GET`, `PROJECT_PROBE` không có biên nhận node; `TASK_BASE_RESOLVE` có bản ghi node `EXECUTING` nhưng chưa terminal. Không dùng ảnh chụp tiến trình hiện thời để suy diễn công việc lịch sử chắc chắn đã kết thúc.
- Ảnh chụp tiến trình thấy đúng **một node gốc logic** gồm hai tiến trình có quan hệ cha–con; gateway listener trỏ tới bản phát hành `ef9f81f`. Chưa có biên nhận hoàn chỉnh cho danh tính và đường chạy **bộ giám sát node hiện hành** theo PID + thời gian tạo + tác vụ khởi động. Có tập tin khởi động cũ `startup-legacy-20261010-081338-4ccf9f36.cmd` tham chiếu mã `4f0d849`, khác bản node ghim `dabce9a`; đây là nguy cơ đường khởi động cũ, **không phải bằng chứng đã tái khởi động sai**.
- Trạng thái quản lý node sản xuất còn `command_quarantine_active=false`, `new_job_admission_allowed=true`. Do đó **khóa phát lệnh khoa học thực địa chưa đạt**. Mã ứng viên có cổng `SCIENCE_DISPATCH_QUARANTINED` và kiểm thử giữ cấu hình sau tái khởi tạo, nhưng không được đánh đồng với trạng thái thực thi hiện tại.

### Cơ chế thu chứng cứ chữ ký có giới hạn, chỉ chuẩn bị ngoại tuyến

Đã bổ sung `tools/v31_operator_capture_authorization.py` và kiểm thử. Cơ chế này **chỉ kiểm định** giấy phép thu hai yêu cầu nhịp tim gốc đã có, không cài bộ thu hay chạm hệ thống thật:

1. Giấy phép riêng bắt buộc có chữ ký Ed25519 của **người vận hành**; khóa công khai kiểm tra phải được ghim **ngoài** giấy phép và gói chứng cứ.
2. Giấy phép buộc ghim định danh thiết bị, thế hệ định tuyến, SHA gateway/node, SHA khóa công khai node, phạm vi **chỉ** hai `POST /device/v1/heartbeat`, thời hạn tối đa 10 phút và tuyệt đối không có quyền thay đổi sản xuất.
3. Nội dung cặp gói thô phải được ghi lại từ kênh quan sát **được phê duyệt riêng**, sau khi gateway xác thực, trong kho cục bộ có kiểm soát truy cập; không sao chép khóa riêng, token hoặc nội dung gói thô lên GitHub.
4. Kiểm lại giao thức ký `RMCPNODE1` trên byte yêu cầu gốc, hai dấu thời gian cách ít nhất 30 giây, hai nonce khác nhau, cùng khóa thiết bị/route/release và các điều kiện an toàn node. **Một chữ ký hợp lệ của node không tự chứng minh gateway đã chấp nhận gói**; phải có bằng chứng độc lập về nguồn capture, phía gateway và sổ lệnh.
5. Ngay cả khi gói/giấy phép hợp lệ: `release_gate=HOLD`, `production_deployment_permitted=false`. Giấy phép chỉ phạm vi bằng chứng, **không phải giấy phép cài bộ quan sát hoặc nâng cấp**.

**Hiện trạng:** không tìm được hai gói gốc qua kênh đọc đã cấp quyền. Không được triển khai bộ nghe mạng, ghi log nhạy cảm hay thay gateway/node chỉ để tạo chứng cứ khi chưa có phê duyệt riêng.

### Tái kiểm định bất biến và hoàn nguyên

- Công cụ `tools/v31_frozen_command_readonly_comparator.py` chỉ kiểm tra tập định danh 19 lệnh, trường chung và ghim bốn lệnh với dữ liệu tổng hợp trong CI; kết quả tối đa `PARTIAL_STRUCTURAL_MATCH`, **không chứng nhận byte-for-byte bất biến và không xác nhận lệnh lịch sử terminal**.
- Hoàn nguyên mô phỏng SQLite và tệp cấu hình đã PASS ở mốc ứng viên trước; còn thiếu kiểm toán tác vụ khởi động Windows, PID/start-time của bộ giám sát, mã nguồn thực sự đang chạy, phương án chuyển tệp nguyên tử và diễn tập hoàn nguyên trên môi trường tách biệt cùng cấu hình tương đương, không đụng tiến trình nghiên cứu.
- Không có quyền tạo công việc RemoteMCP mới chỉ để truy vấn Windows: đường thực thi `run_command` trả rào cản cần `task_job_submit`. Không vượt qua rào cản bằng cách phát công việc mới trong phạm vi HOLD.

### Các điều kiện để được trình quyền nâng cấp riêng

`CI_EXACT_FINAL_SHA=REVALIDATE`; `SIGNED_RAW_PAIR=HOLD`; `FOUR_COMMAND_IMMUTABILITY=PARTIAL_NOT_COMPLETE`; `FOUR_COMMAND_OUTCOME=UNRESOLVED`; `LIVE_WATCHDOG_SOURCE_AND_SINGLETON=HOLD`; `ISOLATED_ROLLBACK_REHEARSAL=HOLD`; `LIVE_SCIENCE_DISPATCH_HOLD=NOT_ACTIVE`; `AUTHORIZATION_TO_CAPTURE_LIVE_PACKETS=NOT_GIVEN`; `NODE_OR_GATEWAY_UPGRADE_AUTHORIZED=NO`.

Chỉ được trình **đề xuất** phê duyệt nâng cấp khi độc lập kiểm định xong tất cả chứng cứ tương ứng. Không được tự động nâng cấp, khởi động lại hay chạy lại các tác nhân nghiên cứu.
