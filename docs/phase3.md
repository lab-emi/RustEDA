# 阶段三：RustLane 联合流程与工具环境

目标是在 RustEDA 根目录驱动已验收的 RustSys → RustROAD → RustSTA 全流程，
由 RustLane 保存配置、输入身份、运行状态与可恢复检查点。

## 实现边界

- 保持各模块的独立 Cargo workspace，不复制共享实现，不新增 EDA 数值算法。
- 原 RustLane post-route WIP 已保存到独立候选分支。旧 `spef_elmore` 判据和简化
  DRC/LVS 解析不能直接代替当前 RustROAD 的完整验收协议。
- RustROAD 完整验证需要 Podman 创建命名空间；RustLane 的原生子进程沙箱有意禁止
  这种操作。使用显式、持久化的外部任务请求/回执协议，由根目录前台 worker 执行。
  原生子进程保护保持原有行为；没有新增后台服务。
- 外部任务运行期间，RustLane 工作区可只读查询。完成/失败提交仍要求匹配 base-rev，
  并复核输入、工具、报告及产物哈希。取消必须清理本次拥有的进程范围和容器；
  清理状态未知不能自动重试或报告成功。
- 工具环境显式锁定可执行文件、Python、GNU time、PDK 文件和固定容器；缺失或变化
  是错误或 unverified，缓存不能把不完整验证变成通过。

## 验收

1. 保存原 WIP，原始同级工作区不变。
2. 从根目录生成配置，执行真实双 PDK 小设计，保留完整门禁、声明偏差和分析范围。
3. 验证零工作缓存、输入/工具/产物变更、失败、取消、恢复和陈旧 revision。
4. 本地与 CI 的协议/进程测试通过；记录独立工具证据、端到端耗时与 RSS。

## 本地验收结果（2026-10-25）

本地实现与验收完成。完整记录见 [phase3.json](../integration/evidence/phase3.json)。
最终 RustLane 提交为 `321e71350f230c6bde9cd5416782146ede5046d3`；
RustROAD 使用 `a6ea5063fc39e1419b760901f1918328f73f065c`。

| 设计 / PDK | 完整 25 项门禁 | 初始化＋运行 | worker 峰值 RSS | 最终版本缓存复用 |
| --- | --- | ---: | ---: | ---: |
| counter / SKY130 HD | passed | 109.273 s | 490.84 MiB | 0.975 s |
| counter / GF180 7T | passed | 151.883 s | 447.01 MiB | 0.955 s |
| fifo / SKY130 HD | deviation_declared | 237.357 s | 511.20 MiB | 1.237 s |

耗时包含输入捕获、校验、前台编排和完整生产者运行，不含编译；缓存复用包含最终 RustLane
版本的依赖清单、文件哈希和完整验收协议复核，三个工作区均保持 `rev2`，执行步骤数为零。
完整三项验收控制器总耗时 506.916 s；RSS 口径见下文。

每项均覆盖 native / OpenRCX 两类 SPEF、TT / SS / FF 三个角、CPU / f64，未约束端点为零。
端点时序最大差值分别为 0.607 / 0.124 / 0.612 ps。FIFO 的 MPW 原工作点最大差值为
1.279 ps，15 条逐行 pulse 记录保留声明偏差和原始证明；没有将其改成普通通过。
物理检查、等价、协同仿真、负控制和确定性重建均属于这 25 项门禁。

其他验收：

- RustLane：31 项测试通过，5 个子进程 fixture 入口由测试间接执行；另行执行的实际 Tcl
  字面量测试通过。最终测试/编译进程峰值 352,192 KiB；格式检查、Clippy 及远端 CI 通过。
- 根仓：20 项编排安全测试通过。已验证输入/工具/产物变化、新增依赖文件、陈旧 revision、
  不完整报告、伪声明、未知清理状态和非受支持线程配置均不能获得成功/缓存通过。
- 实际 SIGTERM / SIGKILL：先断言目标 `.scope` 正在运行，再中断；已验证根入口的失败提交、
  崩溃后的全局拦截、显式恢复和新 attempt。最终为 `rev4`、两个保留的 attempt；无关容器存活。
- 原 RustLane 同级工作区的 tracked patch 和 8 个未跟踪文件逐项哈希一致；归档分支已推送。
- 从 GitHub 递归克隆通过固定版本、共享文件和无操作初始化检查；这是源码入口验证，
  不是在另一台机器上完成物理环境验证。

原始记录在 `.work/phase3/final-flow/`、`.work/phase3/worker-control-accepted/`。
较早的清理探针和一次测试脚本错误保留在 `.work/phase3/`，已在机器记录中标为 superseded。

## 实施前资源预算

- 编排元数据不超过 128 步；捕获文件最多 1024 项，每棵目录树最多扫描 4096 项、深度 32，每项约 256 B，
  加上最多 16 MiB 报告和解析副本，控制器预计低于 128 MiB RSS。
- 所有文件身份通过 64 KiB 缓冲区流式计算；按文件/总字节限制捕获输入，不枚举图路径。
- 真实小设计沿用已验收的 RustROAD 预算：不超过约 5000 pins / 20000 arcs，三个角、
  两个工具线程；内部阶段保留各自边界。历史最大单工具 RSS 约 520 MiB。
- 构建用两个 Cargo jobs、两个测试线程，4 GiB 内存/2 GiB swap/有限时限。
  完整物理 worker 用 8 GiB 内存/2 GiB swap、3600 秒范围，取得 `.work/scale.lock`，
  双 PDK验证串行；其内部 Podman/工具限制继续生效。
- 协议与取消测试使用微型进程，预计低于 256 MiB；每个测试有独立时限和内存边界。
- 每次重型运行前检查 `free -h`；用现有 GNU time 或 `scripts/measure.py` 记录实际 RSS。

尚未扩展到 GUI、工业规模设计、晶圆厂签核或此前仍未关闭的数值研究问题。

## 使用与恢复

根目录 README 提供单设计命令。完整的本地验收可以重复执行，但每次使用新目录：

```sh
systemd-run --user --scope -p MemoryMax=2G -p MemorySwapMax=1G \
  timeout 2400s python3 integration/verify_phase3_flow.py --out .work/new-flow-check
systemd-run --user --scope -p MemoryMax=2G -p MemorySwapMax=1G \
  timeout 180s python3 integration/verify_worker_control.py \
  --out .work/new-worker-check --with-root-flow
```

外层范围约束验收控制器；每个实际 worker 另有 8 GiB / 2 GiB swap 范围和有限时限，
容器保留生产者配置的独立内存上限。报告的 RSS 是所观测到的单个进程/工具/容器峰值，
不是所有容器的瞬时内存总和。根目录开发构建助手的 swap 上限为 2 GiB。

`run` 完成后使用它返回的 `new_rev`。进程被强杀时，先检查
`.work/integration/active-worker.json` 和工作区 `HEAD`，再运行：

```sh
./dev flow --workspace PATH recover --base-rev revN
./dev flow --workspace PATH run --base-rev revNext --retry-failed
```

恢复根据本次请求的进程范围和容器标签清理，不猜测 PID，也不清理其他任务。
如果生产者输入已变化，仍可完成清理并提交失败记录，后续使用新工作区。

## 私有 CI 环境

`.github/workflows/physical-flow.yml` 是手动触发模板，使用 `rusteda-qualified` 专用 Linux runner。
它需要预先配置跨私有仓库的 SSH 只读访问、用户 systemd manager、rootless Podman、固定 OCI
镜像、工具清单匹配的可执行文件及 `RUSTEDA_GNU_TIME`。工具可以通过 PATH 或本机工具别名找到。
PDK 路径仍由 RustROAD 固定的 signoff manifest 指定；其他机器必须提供相同的受验数据路径，
不能只改 manifest 的哈希或跳过校验来获得通过。

用户已确认本阶段先完成本地验收，并交付可启用的 CI 模板。
本阶段未自动部署 runner、下载工具/PDK 或创建凭据。普通根仓 CI 只验证编排安全；
RustLane CI 验证 Rust 合约、进程和 Tcl 字面量。完整物理 CI 在配置 runner 前为 `unverified`。
