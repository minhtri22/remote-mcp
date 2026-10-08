# HANDOFF LETTER — RemoteMCP V3.1: canonical plugin, rolling upgrade, protected CQG-RU long run

**Ngày bàn giao:** 2026-10-08 (Việt Nam, UTC+07).  
**Repo:** `https://github.com/minhtri22/remote-mcp`  
**Nhánh phát hành:** `release/v3-production-recovery`  
**Exact HEAD đã xác minh trên GitHub:** `446e45fce82fbdceb684e21d01ebc88b0981f004` (merge PR #57).  
**Tình trạng tổng quát:** SOURCE MERGED + CI PASS; một release mới đã được stage trên D; **trạng thái triển khai gateway chưa xác minh; node live/cut-over chưa có bằng chứng hoàn tất; Zero-C runtime migration chưa được thực hiện**.

> **Yêu cầu của người dùng cho phiên mới:** Dùng **đúng `@RemoteMCP V2 Web (Canonical)`**, kiểm tra trạng thái thực rồi tiếp tục nâng cấp thật; không bỏ sót backlog. Giữ an toàn long run CQG-RU PID 9152 trong 10–15 ngày và giữ nguyên legacy workspace trên C. Không dùng connector `RemoteDesktop` hay RemoteMCP legacy thay thế.

## 1. BẮT ĐẦU PHIÊN MỚI — ĐÚNG CONNECTOR VÀ QUY TẮC TIẾP TỤC

- Plugin canonical: **`remote-mcp-v2-web-clean`**, ID **`plugins_6ac0d130681481919355d7881f2cc648`**, phiên bản đã thấy **1.5.2**. Đây là plugin user sẽ tự chọn/attach khi mở phiên chat mới. Plugin có trạng thái *installed* nhưng ở phiên cũ **không có bộ tool canonical được gắn vào phiên**, khiến agent sai đường sang `RemoteDesktop` và gây hộp thoại cấp quyền RemoteDesktop không sử dụng được. Việc *installed* không có nghĩa session/auth đã hoạt động.
- Tuyệt đối **không fallback** sang `RemoteDesktop`, `remote-mcp-v2-web`, `remote-mcp-v2bd` hoặc connector legacy; **không re-pair** `machine-1` và **không tạo device identity mới**. Nếu canonical tools vẫn không hiện, báo **PLUGIN_SESSION_BINDING_FAILURE** và hướng dẫn user attach/reconnect đúng plugin; không tuyên bố đã deploy khi chưa kiểm tra.
- Đầu phiên: đọc skill của canonical plugin nếu khả dụng; gọi canonical `device_list` → `device_status`, rồi đọc `task_status`, `task_job_get` và log bằng đường được hỗ trợ. Phải phân biệt **connector session**, **gateway read/control plane**, **node heartbeat**, **node command/execution**; cái này hoạt động không chứng minh cái kia hoạt động.
- Các lần trước gặp Cloudflare **524** tại `remote.threadon.xyz/authorize`, `internal error`, `timeout`, `remote command failed`, `safety block`. Nếu tái hiện, thu thập bằng chứng và phân loại đúng, không khởi động lại Windows, không dựa duy nhất vào trạng thái ONLINE.
- Giao tiếp với user bằng **tiếng Việt**; thuật ngữ tiếng Anh chuyên môn đặt trong ngoặc sau tiếng Việt khi cần. Kết thúc mỗi báo cáo nêu **bước hợp lệ tiếp theo**.

## 2. HAI INVARIANT TUYỆT ĐỐI

### 2.1. Bảo vệ long run khoa học CQG-RU

- **PID 9152**, workload **CQG-RU / RU0_C_U3**, thời gian chạy dự kiến **10–15 ngày** (ETA người dùng ước tính **15–22/10/2026**; chỉ là ước lượng). **Không kill, suspend, restart, re-parent, reboot Windows, restart toàn bộ process tree, thay môi trường/source/output, hoặc tiêu tốn tài nguyên ảnh hưởng job.**
- Last verified OS audit trước khi đứt kết nối: PID 9152 `python.exe`, `Responding=True`, `StartTime=2026-10-06 07:38:15 +07`, executable `C:\Users\minht\miniforge3\python.exe`, `ParentProcessId=24528`, CPU tích lũy khoảng **79.367 giây** tại thời điểm đo. Parent `24528` là Python từ `D:\WORK\RESEARCH\2.CQG-RU0G\.local\RU0_G_ENV\Scripts\python.exe`; ancestor cao hơn PID `33756` đã không còn trong lần kiểm tra. Một lần kiểm tra khác báo không có child process cho 9152. **Đây là snapshot lịch sử, phải audit lại PID + start time ngay trước và sau deploy, không coi PID đơn lẻ là identity vĩnh viễn.**
- **Chưa có OS-level inventory đầy đủ tất cả node/watchdog tại cùng thời điểm** để chứng minh độc lập tuyệt đối với *đúng node process sẽ được thay*. Parent chain 9152 không cho thấy node RemoteMCP, nhưng **gate cuối vẫn phải chứng minh quan hệ hai chiều theo live process tree**, đặc biệt descendant/ancestor của process mục tiêu. Không đánh đồng hai Python process lồng nhau với hai node khác nhau nếu chưa kiểm lệnh khởi chạy/identity.
- Output khoa học dự kiến: `D:\WORK\RESEARCH\2.CQG-RU0G\.local\RU0_C_U3\RU0_C_U3_RAW_EVIDENCE.json`. Source chỉ ghi output sau khi chạy toàn bộ; thiếu file `.json`/`.tmp` trong khi PID còn sống **không chứng minh job hỏng**. Không rerun long run, không thay bằng thí nghiệm mới, không đổi seed/bằng chứng.
- Old control plane có lúc báo `authoritative_active_node_jobs=0` trong khi PID 9152 vẫn sống. **Durable capacity ≠ OS process truth**. Không dùng số 0 để cho phép restart.

### 2.2. Giữ nguyên C legacy workspace, D canonical cho công việc mới

- **Legacy compatibility workspace — GIỮ NGUYÊN:** `C:\Users\minht\RemoteMCP-Workspace`. **KHÔNG xóa / di chuyển / prune / đổi tên / sửa bulk / dọn dẹp**. Nhiều agent cũ, handoff cũ hoặc tác vụ giữa chừng có thể còn tham chiếu. Kể cả sau khi PID 9152 kết thúc cũng **không** tự động xóa workspace C này; chỉ cleanup khi có contract/approval riêng.
- **Canonical project/workspace root cho công việc mới:** `D:\WORK\RESEARCH`. Riêng SIX: **`D:\WORK\RESEARCH\SIX`**, không phân tán worktree sang chỗ khác.
- Task/project lịch sử đã bind C tiếp tục C; project/task mới mặc định D. Không silently rebind, không tự copy/sync hoặc giả định C và D là cùng trạng thái. Không cho task tự chọn root tùy ý; C phải nằm trong allowlist legacy rõ ràng.
- Khác với workspace C, **runtime/venv/log/cache/control cũ trên C** có kế hoạch Zero-C riêng, nhưng **chưa được move/xóa khi còn worker/process phụ thuộc**. Runtime migration và legacy workspace preservation là **hai contract khác nhau**.

## 3. DEVICE, NHẬN DẠNG VÀ MÔI TRƯỜNG LIVE TRƯỚC ĐÓ

- Device name: `machine-1`; hostname `DESKTOP-4PSD0G2`.
- `device_id`: `dev_dd73ebfa742f468f2d212bade88c175b`.
- Fingerprint: `b657d5395e393e0957a9ed358bb5a1fe1588fde5d3a44be71295a68d1e73def9`.
- `route_generation=1`. Không re-pair, không thay identity, fingerprint, generation.
- Last verified *old live node release* **`80fe818e5240efd029dfb5e5f6fef53eda496c05`**, source `D:\2.RemoteMCP-releases\80fe818`, runtime legacy `C:\Users\minht\AppData\Local\RemoteMCP\runtime`, Node venv cũ `C:\Users\minht\AppData\Local\RemoteMCP\node-venv`. **Các số này là snapshot trước lần thử deploy gateway; phải đọc lại attestation, không tự tuyên bố hiện vẫn như thế.**
- Trước đây có snapshot `Watch-RemoteMCP-Node` Powershell PID 44008, `python -m remotemcp.node` PID 33276, Python PID 43728 child của PID 33276. Các PID này **đã cũ**, chỉ dùng làm manh mối audit, **không kill theo ID cũ**.
- Trong ảnh đã có probe của OW1K: `CWD=C:\Users\minht\RemoteMCP-Workspace\SIX`, `IS_CANONICAL=False`, `RELATIVE_RUNNER_EXISTS=False`, `D_RUNNER_EXISTS=True`. Central/heartbeat có lúc khai `execution_root=D:\WORK\RESEARCH` và `node_root_rel=SIX`. Đây là **execution-context mismatch thực nghiệm**, không phải thiếu file runner. Không kết luận chắc chắn “duplicate node” nếu chưa OS audit toàn bộ.

## 4. SOURCE: CÁC PR ĐÃ MERGE, EXACT SHA VÀ KẾT QUẢ

GitHub đã được kiểm lại trong phiên bàn giao, tất cả các PR sau đều **MERGED**:

| PR | Nội dung | Merge commit |
|---|---|---|
| #50 | Hạ tầng Zero-C cho machine-1 (**source**, chưa có nghĩa đã migrate live) | `5e3f9f98986cd49f29ec91ea16167dab80e6983c` |
| #51 | Cổng an toàn process vật lý khi restart/upgrade; snapshot TTL riêng 45 s; heartbeat scanner bất đồng bộ | `5bda281843704c8be61c1727bbd96fa5dc037d8b` |
| #52 | Truyền biến môi trường managed execution: `NVIDIA_API_KEY`, allowlist có kiểm soát, Windows persistent env fallback | `6073914ff1b74d92617c2aab7a6995178dcb2e06` |
| #53 | Khôi phục bằng chứng kết thúc predecessor routed job theo exact `node_job_id`/provenance, không rerun | `c8bf70ddc4d27c115f3a8dd4062d90a1bb2e12ea` |
| #54 | `NON_GIT` bootstrap → exact Git materialization (staging, exact SHA, no task-state overwrite) | `970cf52ede254ccbfacefbb48fd76bd16562fb63` |
| #56 | Dual-root: canonical D + approved legacy C; source ban đầu bị regression API | `d0d6979c61a7610bab4a3c8c33596dc761012cf7` |
| #57 | Sửa regression công khai `node_root_rel`; giữ `repo`, `SIX` thay vì lộ `@root/CANONICAL/...`; truyền `LegacyRootDirs` qua watchdog/supervisor | **`446e45fce82fbdceb684e21d01ebc88b0981f004`** |

**Lưu ý:** PR #56 đã merge dù CI FAIL vì `node_root_rel='@root/CANONICAL/repo'` thay vì `'repo'`. PR #57 sửa đúng regression đó. GitHub đã xác nhận HEAD `release/v3-production-recovery` khi bàn giao là **`446e45fce82fbdceb684e21d01ebc88b0981f004`**; không dùng moving HEAD nếu repo đổi về sau.

**QA cuối PR #57** (PR head `b8ea19f86fb1ff290df8d14f9a0d31f0dc2787a7`) đã xác minh trên GitHub; tất cả **completed/success**:

1. Zero-C node infrastructure QA #33 — PASS.
2. RemoteMCP Node Supervisor Static QA #51 — PASS.
3. Canonical and legacy root compatibility QA #7 — PASS.
4. Physical process safety QA #30 — PASS.
5. Branch serial job admission implementation QA #120 — PASS.

Các bài kiểm thử đa hệ điều hành Windows + Ubuntu gồm hồi quy V2-B/V2-BD. **SOURCE QUALIFIED không đồng nghĩa GATEWAY DEPLOYED / NODE DEPLOYED / ZERO-C COMPLETE**. Nếu thêm source commit sau mốc này phải qualify lại.

## 5. HÀNH VI SOURCE ĐÃ IMPLEMENT — CẦN KIỂM THỬ LIVE

- **Branch-serial:** khóa theo `task_id`, **không có khóa toàn machine hoặc toàn project**. Task khác nhau có thể chạy song song theo capacity; tuyệt đối không nói long run dự án A chiếm “đường thực thi duy nhất” của machine-1.
- **Physical-process safety:** từ chối nâng cấp nếu scan process chưa rõ/stale; TTL process snapshot 45 s, không chỉ tin durable registry.
- **Managed environment:** `NVIDIA_API_KEY` mặc định trong allowlist, có `REMOTEMCP_MANAGED_ENV_ALLOWLIST` cho biến được operator cho phép; Windows đọc fallback User/Machine persistent env khi node lâu đời chưa nhận key; không leak toàn bộ environment. Kiểm bằng `PRESENT/ABSENT`, **không in secret**.
- **Routed predecessor recovery:** `JOB_RECOVER_ROUTED_JOB` cần exact `node_job_id`, operation/task/project/provenance proof; mismatch → `PREDECESSOR_STATE_UNRESOLVED`; không rerun hoặc tự mark terminal.
- **NON_GIT → Git:** staging sibling, xác minh remote/ref/40-character SHA, giữ project_id/binding; **cấm chuyển nếu project đã có task**. GWF cũ có thể cần reconcile riêng.
- **Dual-root:** `node_root_rel` công khai chỉ là relative path như `repo`, `SIX`; root namespace ở metadata nội bộ; CLI `--legacy-root`, node configs/worktrees/jobs, script khởi động và watchdog/supervisor có forwarding `LegacyRootDirs`. Phải xác minh sau deploy và cả lúc watchdog tự phục hồi.

## 6. ĐÃ STAGE TRÊN D — CHƯA PHẢI LIVE DEPLOY

Một maintenance job riêng đã báo `STAGED` với exact SHA và Python compile PASS:

```text
D:\WORK\RESEARCH\.remotemcp\machine-1\source\446e45fce82fbdceb684e21d01ebc88b0981f004
D:\WORK\RESEARCH\.remotemcp\machine-1\venv-446e45f
D:\WORK\RESEARCH\.remotemcp\machine-1\logs
D:\WORK\RESEARCH\.remotemcp\machine-1\tmp
D:\WORK\RESEARCH\.remotemcp\machine-1\cache
D:\WORK\RESEARCH\.remotemcp\machine-1\control
```

- Staging: Git clone/fetch SHA/checkout detached, tạo venv riêng, `pip install httpx==0.28.1 cryptography==46.0.6`, `py_compile` node/durable. **Chưa chứng minh đủ dependencies/import/runtime smoke**; phải kiểm trước cut-over.
- `task_id=tsk_cde9d9eebcab7e6c5024399a`, `proxy_job_id=rjob_c1baa312e588e3c858d6160ed807a717`. Đường dẫn và hash phải được đọc lại trước sử dụng.
- **Không xóa source/venv/runtime cũ hoặc workspace C vì đã stage bản mới.**

## 7. GATEWAY DEPLOY: ĐÃ SUBMIT NHƯNG OUTCOME CHƯA XÁC MINH

Đây là **điểm tiếp tục quan trọng nhất**, không được bỏ qua. Trước khi connector lỗi, đã submit một managed job chạy:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  D:\WORK\RESEARCH\.remotemcp\machine-1\source\446e45fce82fbdceb684e21d01ebc88b0981f004\Update-RemoteMCP-Gateway.ps1 `
  -SourceRepo D:\WORK\RESEARCH\.remotemcp\machine-1\source\446e45fce82fbdceb684e21d01ebc88b0981f004 `
  -Ref 446e45fce82fbdceb684e21d01ebc88b0981f004 `
  -SkipFetch
```

- `operation_id=gateway-deploy-446e45f-20261008-r1`.
- `task_id=tsk_14cb1f7548d7358c43222b9c`.
- `proxy_job_id=rjob_e3d7529a2653b98cedbdb8299547a54d`.
- Sau submit, `task_job_get` và `device_list` qua RemoteDesktop lỗi/đứt connection. User gửi ảnh **“Không thể kết nối với RemoteDesktop. Vui lòng thử lại sau”**, yêu cầu canonical plugin.
- **Chưa có authoritative terminal result, stdout/stderr, release snapshot, health/rollback receipt, gateway deployed commit attestation** cho job này. Nó có thể success, còn running, rollback hoặc fail. **TUYỆT ĐỐI KHÔNG giả định đã deploy, không chạy updater lần nữa một cách mù.**
- Phiên mới cần đọc *existing operation/job* và logs/attestation bằng đúng canonical plugin. Xác minh gateway release **riêng** với node release. Nếu updater còn in-progress hoặc rollback, không đè một updater khác.
- **Chưa có bằng chứng node live đã cut-over**. Chưa thực hiện đầy đủ Zero-C runtime migration.

## 8. MAINTENANCE TASKS VÀ OS AUDIT ĐÃ LÀM

Các maintenance task **tách khỏi science**:

- `tsk_def04b15487ded8a83b9b74f`: maintenance đời đầu; từng chuyển `RECOVERABLE`, predecessor blocker trên live node cũ. Không reclaim/rerun mù.
- `tsk_16397fea79ec41b8f6fa682f`: audit PID 9152, PASS; sau đó lane có predecessor.
- `tsk_e0dbaef615ff7b52e4c5778d`: parent-chain audit; parent 24528, ancestor 33756 không còn.
- `tsk_cde9d9eebcab7e6c5024399a`: stage release, báo PASS.
- `tsk_14cb1f7548d7358c43222b9c`: **gateway updater submission; outcome UNKNOWN**.
- Maintenance/stability canary project: `prj_b65ab0cb8eaa49309915a5d6`. Không ghi science outcome vào đó hoặc dự án nghiên cứu.

Các lần audit rộng command line có lúc bị safety layer chặn; **không lách chính sách bằng legacy shell**. Khi canonical route cho phép, audit đầy đủ `Watch-RemoteMCP-Node`, tất cả `python -m remotemcp.node`, cha/con, executable, commandline, runtime, `--root`, `--legacy-root`, start time, venv/source và scheduled task watchdog. Không kill theo PID cũ hay regex một cách mù.

## 9. KẾ HOẠCH CÒN LẠI — THỨ TỰ CÁC GATE

### GATE A — Plugin canonical + stable read

1. Chứng minh canonical plugin thật sự attached/authenticated, read thành công, không phải RemoteDesktop.
2. `device_list` → `device_status`: identity, fingerprint, generation, heartbeat, gateway/node version, device commands/capacity.
3. **Đọc existing gateway updater** `operation_id=gateway-deploy-446e45f-20261008-r1`, `rjob_e3d7529a2653b98cedbdb8299547a54d` (task `tsk_14cb...`) + stdout/stderr + updater snapshot/health/rollback receipt. Nếu vẫn running/unknown, không submit lại.
4. Chạy thử readback sâu theo đường chính thức, không chỉ đọc registry, vì trước đây `remote command failed`, 524, internal error và timeout.

### GATE B — Protected process & node audit

5. Kiểm lại **PID 9152 + start time** + commandline/exe/output, responsiveness; audit parent 24528, mọi child hiện tại.
6. Liệt kê mọi watchdog/node processes thật đang sống; các tham số `--runtime-dir`, `--root`, `--legacy-root`, source/venv và parent-child; phân biệt Python launcher với duplicate node thực.
7. Chứng minh **PID 9152 không là ancestor hoặc descendant của đúng process dự kiến thay**, kiểm worker/runtime còn active. Pin PID + creation time của process được phép thay, không dùng PID snapshot cũ.
8. Xác minh stage SHA trên D, dependencies/import/doctor, quyền/đĩa/CPU/RAM; backup/rollback có thể thực thi; không ảnh hưởng CQG-RU.

### GATE C — Rolling deploy có rollback

9. Nếu gateway updater đã thành công và health/attestation PASS: không deploy lại. Nếu fail/rollback: điều tra và thực hiện controlled recovery theo receipt, không spam retry.
10. Chỉ sau A/B PASS mới cut-over **node chính xác**, một device identity; exact source `446e45f...`; canonical `--root D:\WORK\RESEARCH` và explicit legacy `--legacy-root C:\Users\minht\RemoteMCP-Workspace`; **old runtime phải giữ nguyên nếu còn worker đang dùng**. Không tạo hai node cùng identity.
11. Kiểm watchdog/supervisor phục hồi node với cùng `LegacyRootDirs`. Đặc biệt `Start-RemoteMCP-Node.ps1 -Restart` có thể dừng các node process matching, **không chạy unguarded**; không đụng PID 9152.
12. Xác minh post-cutover: identity/fingerprint/route_generation, gateway/node attestation, heartbeats, stable read/command path, PID 9152 cùng start time còn sống, old runtime/source/venv và C workspace vẫn nguyên, không split-brain writer.

### GATE D — Smoke/agent unblock

13. Tạo zero-science task **mới** trên canonical D: `CWD` đúng `D:\WORK\RESEARCH`, check worktree/runner. Task lịch sử legacy C vẫn ở C, public `node_root_rel` là relative path không `@root/...`.
14. Test submit→get→logs→result, concurrency giữa khác `task_id`, physical process safety, `NVIDIA_API_KEY=PRESENT/ABSENT` không lộ secret.
15. Test predecessor recovery read-only từ evidence cũ, không rerun scientific job.
16. Chỉ chuyển `NON_GIT→GIT` khi thỏa zero-existing-task guard; nếu có task lịch sử thì phải reconcile/migration plan riêng, không bypass.
17. Ghi deployment receipt chính xác: pre/post SHA, gateway/node runtime, PID pre/post, legacy C/D checks, QA và rollback state; thông báo user.

### GATE E — Zero-C full migration *sau* long run

18. Không migrate runtime/venv/log/cache/control legacy còn có writer; đợi các process/worker phụ thuộc kết thúc rồi audit riêng (CQG-RU PID 9152 10–15 ngày là protected).
19. Legacy **workspace** `C:\Users\minht\RemoteMCP-Workspace` vẫn được giữ ngay cả sau khi runtime đã chuyển. Muốn cleanup phải có authorization độc lập.

## 10. BACKLOG / CÁC AGENT-PROJECT BỊ CHẶN — KHÔNG BỎ SÓT

### P0 — Hoàn tất deploy an toàn, chưa có evidence để formal-close

- [ ] Canonical plugin attach/auth + stable gateway/node read path.
- [ ] Authoritative result **gateway updater đã submit**, health/rollback/attestation; không lặp operation chưa rõ.
- [ ] OS inventory node/watchdog/process tree và proof bảo vệ PID 9152 cho đúng process được thay.
- [ ] Kiểm staged release SHA + venv dependencies + rollback readiness; pin release exact.
- [ ] Rolling node cut-over giữ identity, canonical D + approved legacy C, bảo vệ runtime cũ và long run.
- [ ] End-to-end smoke read/submit/log/result; signed physical process; C/D mapping; PID9152 before/after; báo cáo deployment receipt.
- [ ] Không false-PASS: phân biệt `SOURCE MERGED`, `CI PASS`, `STAGED`, `GATEWAY DEPLOYED`, `NODE DEPLOYED`, `RUNTIME MIGRATED`, `ZERO-C COMPLETE`.

### P1 — Unblock từng agent/project sau P0

- [ ] **SIX OW1K**: project lịch sử `prj_0b518fdd7b6ab4cfdf86cd09`, lúc kiểm là `NON_GIT`; probe task `tsk_1b2e36abd8e097e6b78b4b3f` chạy CWD ở `C:\Users\minht\RemoteMCP-Workspace\SIX`, runner thật ở D. **Không xóa C**. Audit existing binding (task cũ tiếp tục C), tạo/use đúng canonical D project/task cho thực thi mới nếu được phép. D path là `D:\WORK\RESEARCH\SIX`. Chứng minh `relative_runner_exists=True` trong **đúng task canonical**. Không chạy OW1K science trước execution lock riêng.
- [ ] **GWF VNEXT P4**: canonical `D:\WORK\RESEARCH\4.GWF-VNEXT`, repo `minhtri22/GWF`, branch `pivot/gwf-autonomous-research-stack-v1`; project `prj_f65cb1dc522d61a293467983` / `prj_f65...` có bootstrap-only `NON_GIT`; `task_job_submit` bị chặn. P4 prereg/contract đã hoàn tất, implementation chưa PASS. Audit count existing tasks, materialization guard; không né bằng workspace khác, không gán science FAIL.
- [ ] **Routed predecessor**: `machine-1 ONLINE` nhưng gateway không có authoritative predecessor terminal, successor bị chặn. Sau deploy dùng exact recovery của PR #53, kiểm provenance và identity, không tự release task lane, không rerun predecessor. Maintenance task cũ có thể cần safe recovery.
- [ ] **OOC job** `job_092e95190f30219251b98ae3705a8c39`: từng bị safety block/timeout/internal error khi readback. Refresh **existing job only** qua canonical; không rerun/submit replacement, không gọi scientific FAIL khi read path lỗi.
- [ ] **CQG J4.2**: task từng BLOCKED `tsk_be4a4c6fde6392852f505973`, project `prj_bbf6a4d0e0226b48cc56b9b9`; R internal PASS, static preflight 21/21, `execution_locked=false`. Chưa mở DV1/DV2/fresh science; phải re-audit frozen authority, exact commit, zero-science readback, execution lock sau infra recovery. Kiểm trạng thái thực của task/branch trước kết luận.
- [ ] Những task TNR/CQG/ARN/SIX/ArcLLM khác: xét **theo `task_id`**, không giả định long run của dự án khác chiếm machine-wide mutex; không hủy job khác để mở slot.

### P2 — Hậu bảo trì

- [ ] Cập nhật README/runbook/tài liệu kỹ thuật bằng **receipt deployment thật**; source đã có `docs/V3_1_TECHNICAL_ARCHITECTURE_AND_ROLLING_UPGRADE.md`, cần đối chiếu live actual.
- [ ] Quan sát vài chu kỳ heartbeat, tested watchdog recovery **bằng fixture riêng** không dùng CQG-RU làm thử nghiệm; verify vẫn truyền `LegacyRootDirs`.
- [ ] Full Zero-C **runtime** migration sau khi tất cả worker/process cũ terminal & QA/pass safety; không xóa workspace C.
- [ ] Bất kỳ cleanup workspace cũ đều phải lập inventory/agent-reference/hash/handoff và xin approval riêng.

## 11. QUẢN TRỊ KHOA HỌC VÀ BÁO CÁO

- GitHub exact commit là source-of-truth cho **source**, còn live OS/gateway/node attestation là source-of-truth cho **deploy**. RemoteMCP từng không ổn định, nên phải kiểm lại bằng OS evidence khi được phép, không chỉ tin control registry.
- Science: preregistration → implementation/static preflight → execution lock → one-shot → independent QA/adjudication. Không false-PASS, không thực hiện experiment mới để giải quyết lỗi hạ tầng.
- `lineage.md` **append-only**, chỉ science PASS/FAIL đã adjudicate; không ghi CI failure, infrastructure regression/fix, process audit, connector issue, deployment.
- Không sửa scientific evidence đã khóa; không rerun one-shot; không xóa historical task/project hay silently rebind; fail-closed nếu mismatch.
- Nếu deploy thất bại: rollback control plane có bằng chứng, **không kill PID9152 hoặc xóa C legacy**, báo user checkpoint rõ ràng.

## 12. HÀNH ĐỘNG ĐẦU TIÊN KHI MỞ CHAT MỚI

**Không submit deploy mới hay science job ngay.** Chạy lần lượt:

1. Xác minh đúng `@RemoteMCP V2 Web (Canonical)` đang attached, tool canonical khả dụng.
2. Đọc **gateway updater** đã submit: `operation_id=gateway-deploy-446e45f-20261008-r1`, `proxy_job_id=rjob_e3d7529a2653b98cedbdb8299547a54d`, logs/health/rollback/attestation.
3. Đọc machine-1 status/identity và live gateway/node SHA, root/runtime; online không có nghĩa đã upgrade.
4. Nếu path ổn, OS audit PID9152 + mọi node/watchdog process; pin process trước restart.
5. Tiếp tục từ checkpoint thực tế, triển khai đúng exact-qualified commit `446e45fce82fbdceb684e21d01ebc88b0981f004`, bảo vệ long run và workspace C; full Zero-C về sau.
6. Báo user sau mỗi gate: PASS / FAIL / UNRESOLVED + evidence + bước hợp lệ tiếp theo.

**Câu lệnh khởi đầu cho agent mới:**

> Đọc toàn bộ handoff này. Dùng duy nhất @RemoteMCP V2 Web (Canonical). Trước hết xác minh kết quả thực tế của gateway updater đã gửi (`rjob_e3d7529a2653b98cedbdb8299547a54d`), gateway/node live attestation và PID9152/process tree. Không rerun updater mù, không kill/restart PID9152, không đụng `C:\Users\minht\RemoteMCP-Workspace`. Chỉ sau gate PASS mới tiếp tục rolling node cut-over từ exact commit `446e45fce82fbdceb684e21d01ebc88b0981f004`, canonical D + legacy C, smoke tests và backlog. Báo PASS/FAIL/UNRESOLVED từng gate.

**Kết luận:** Source đã QA PASS; staging đã báo PASS; gateway deployment đang **UNRESOLVED vì không có terminal receipt**; node cut-over/Zero-C chưa xác minh. Ưu tiên tối cao: **đúng canonical plugin → reconcile existing updater → audit protected OS process → rolling upgrade an toàn → phục hồi các agent**, không đánh đổi long run 10–15 ngày và legacy C workspace cho một tuyên bố “DEPLOYED” không có bằng chứng.