**给 Codex 的接手 prompt：RustEDA 性能与 QoR 迭代（Ibex / IHP SG13G2 / EZ130，含 NVIDIA cuda-rust GPU 加速）**

本文写于 2026-10-30 19:50 CET，作者 Claude。读者是接手迭代的 Codex/GPT。你的任务是读现场、改代码、跑验证，把下面的路线图逐项推进。不要只停在建议上，也不要把某一步通过当成整体成功。

**目标**

- 不降低 QoR、不放松任何签核门。
- 把 RustEDA 在 Ibex SG13G2/EZ130 上的端到端时间从 2,936.6 s 大幅降下来。
- 把布局布线 QoR 追平 LibreLane。
- 在此基础上，用 NVIDIA/cuda-rust（cuda-oxide）加速确实有收益的计算核。

**路径缩写**（只是写法缩写，不是 shell 变量）：

| 缩写 | 路径 |
| --- | --- |
| `ROOT` | `/home/cgao/git/RustEDA` |
| `P` | `ROOT/.work/ibex-sg13g2-bringup-20261028` |
| `R16` | `P/run16/public-workspace/steps/rtl2gds/attempt1/output`（已发布的 RustEDA run16，**只读证据**） |
| `L16f` | `ROOT/.work/ibex-closure-a2a-20261029/reference-benchmark/reference-run16f`（已发布的 LibreLane 样本，**只读证据**） |
| `A` | `ROOT/.work/perf-analysis-20261030`（本文用到的 QoR 分析脚本） |
| `ROAD` | RustROAD 源码，分支 `claude/ibex-closure-v21` @ `f93d026`（引擎代码 `0813571`，产出 `P/bin/rustroad-v24`，sha256 `0889b370…`） |

本文的数字都是现场测量值，除非明确标成"估算"。估算是给你定目标用的，不能写进任何发布物。

---

## 0. 一页结论

### 时间

2,936.6 s 里，整个 RustEDA 后端几乎都在单核上运行：分配了 16 个 CPU，每个 step 的 CPU 利用率只有 93–114%。四大耗时如下。

| 排名 | 环节 | 耗时 | 问题 |
| --- | --- | --- | --- |
| 1 | 原生 RC 提取（跑两次） | ≈542 s + 538.6 s | 单线程；每个 wire run 都对 7 个布线层做宽窗口 bin 查询，work 5.6×10¹⁰（预算 10¹¹ 已用 56%）；天线修复后又全量重提一次。OpenRCX 提取同一版图只要 9.6 s。 |
| 2 | 原生天线修复 | 858.0 s | 439 个串行子进程，52 个全量 revision，写了 750 MB JSON。最后停在 `attempt_budget`，原生模型仍报 217 条未解决，而 foundry 天线 deck 在同一版图上是 **0 违例**。大部分时间花在保守模型的假阳性上。 |
| 3 | 参考签核串行 | 649.2 s | KLayout main / antenna / LVS 与 OpenRCX+OpenSTA 彼此独立，却串行运行。 |
| 4 | 布线 | 203.2 s | 单线程。 |

**不改任何算法结果、产物 SHA-256 不变的前提下，总时间可以降到约 850 s（估算）。**

### QoR

**面积（+11,387 µm²）：唯一来源是 well tap。**

- RustEDA 13,923 个，LibreLane 6,900 个，多出 29,733 µm²。
- 不算 tap 的话，RustEDA 的标准单元面积比 LibreLane **小 18,346 µm²**。
- RustSys 把使能触发器映射为 `DFFMRQX2`，每个比 `DFFRQ+MUX2` 省 4.23 µm²。

**线长（+600,837 µm）：大约 65% 来自布局，35% 来自布线绕行。**

- 用同一定义算 HPWL：RustEDA 1,300,746 µm，LibreLane 954,776 µm（+36%）。
- 布线长度与 HPWL 之比：1.290 vs 1.129。

**通孔（2.32×）：多出的全在 Via2 到 TopVia2，Via1 持平（58,521 vs 58,758）。**

- M2 线长持平，多出的 542k µm 全在 M4/M5/TopMetal。
- DEF 里能看到大量阶梯形走线：每个拐角一对 via。
- 根因候选：
  - weighted A* 权重为 4；
  - via 代价只有 840 DBU（等于 2 个 track pitch）；
  - 没有拐弯代价；
  - `access_layer` 设为 Metal3；
  - 层代价不随层高变化。

**时序：不区分两边。** 100 ns 时钟下两边 setup 余量都约 48–49 ns，hold 都为正。

### GPU

- cuda-rust 的 SIMT 路径 cuda-oxide 在本机可用：RTX 4090 Laptop，sm_89，driver 615.78。
- RustSpice 已有可直接照搬的先例：同一份 Rust 源码编译两次，一份是 rayon `cpu_ref`，一份是提交进仓库的 PTX。
- 对 Ibex，GPU 最大的直接收益是 RC 提取里的整数几何核；布局的电场密度核是在布局算法升级之后做规模化加速用的。
- GPU 不是 Ibex 的第一杠杆：前面约 3.5× 的提速来自去掉串行和进程开销，不来自 GPU。

---

## 1. 不可放松的要求

- 设计不动：
  - 两块真实 `RM_IHPSG13_1P_1024x32_c2_bm_bist`（`u_imem`、`u_dmem`）；
  - 100 ns 时钟，floorplan 不变；
  - 最终 density ≤ 70%（`integration/ibex_sg13g2_ez130/final_density.py`）。
- 16 个 gate 全部通过才算完成：
  - foundry main DRC 0、antenna 0；
  - full-device LVS 通过；
  - vendor-model 仿真通过；
  - 综合和最终等价性都是 proven；
  - OpenRCX+OpenSTA 时序在 TT/SS 都无违例；
  - RustSTA 与 OpenSTA 在同一 SPEF 上的差异 ≤ 1 ps。
- 诚实性：
  - 不允许 waiver，不许删约束或宏，不许伪造报告；
  - 没跑的检查报 `unchecked`，不能报 0；
  - 超时或失败一律算未完成。
- Clean-room（AGENTS.md Rule 1）：
  - Yosys / OpenROAD / OpenSTA / KLayout / OpenRCX / Magic 只能当黑盒用；
  - 不读、不移植、不 diff 它们的源码，也不用它们的源码解释差异；
  - 本文对 LibreLane 的所有描述都只来自它的日志和输出文件，你也只能这样做；
  - 公开论文里的算法（ePlace/Nesterov、PathFinder 式协商布线、B2B 等）可以按论文从零实现。
- 每个 GPU 核都要先有 `cpu_ref`，并配一个与 `cpu_ref` 的差分测试（Rule 5）。
- 确定性模式下，并行规约用定点 i64 或固定顺序（Rule 6）。
- 资源（Rule 9）：
  - 动手前先做内存估算，写进 commit 或 HANDOFF；
  - 重活放在 `systemd-run --user --scope -p MemoryMax=… -p MemorySwapMax=… timeout …` 里跑；
  - 估算超过 4 GiB 的加 `flock ROOT/.work/scale.lock`；
  - 用 `/usr/bin/time -v` 记录 peak RSS；
  - 别杀其他会话的进程。
- 已发布证据只读：
  - 不写 `R16`、`L16f`、`P/run16`、`ROOT/.work/ibex-closure-a2a-20261029/a2a16` 里的任何东西；
  - replay 一律先复制到新目录。
- Git：
  - 不 push，除非用户要求；
  - 不 reset、不 clean `modules/RustROAD-bringup` 以及其他脏工作区（见第 8 节）；
  - 不建 root Cargo workspace；
  - 不裸用 `git stash`。
- 设备：Matter 插座永远不 toggle；Chromium 不加 `--no-sandbox`。

---

## 2. 基线数据

### 2.1 结果对比（已发布：`docs/benchmarks.md`、`docs/benchmarks/ibex-sg13g2-ez130.json`）

| | RustEDA run16 | LibreLane 3.0.17 run16f |
| --- | --- | --- |
| 结局 | 16/16 gate 通过 | foundry main DRC 失败（1 × M2.b） |
| 原始 SV → 最后一个 gate | 2,936.6 s | 2,790.0 s 时停止（antenna/LVS/仿真/CEC 都没跑） |
| 峰值单进程 RSS | 2.16 GiB（**这是 KLayout antenna deck 的，不是 RustEDA 引擎**） | 2.94 GiB |
| RustEDA 引擎自身最大 RSS | 1.05 GiB（`be_route`，1,099,140 KiB） | — |
| 标准单元面积 | 233,909 µm² | 222,522 µm² |
| 布线信号线长 | 1,678,599 µm | 1,077,762 µm |
| Via | 301,859 | 130,286 |
| TT setup / hold WS | 49.293 / 0.136 ns | 48.477 / 0.158 ns |
| SS setup / hold WS | 48.877 / 0.261 ns | 47.640 / 0.291 ns |

### 2.2 RustEDA run16 时间分解（`R16/step-receipts.json` 加各 `R16/logs/*.time`）

所有 step 的 wall time 合计 2,913.8 s，另有 prepare 12.7 s 和启动开销。

| Step | 内容 | Wall (s) | 占比 | CPU% | Max RSS |
| --- | --- | ---: | ---: | ---: | ---: |
| `fe_*`（95 个） | RustSys 综合/映射、ECO、三轮 place+CTS、RustSTA | 68.8 | 2.4% | — | — |
| `be_route` | RustROAD 布线 | 203.2 | 7.0% | 99% | 1.05 GiB |
| `be_postroute` | 复制 trial workspace、**原生提取（≈542 s）**、RustSTA | 560.6 | 19.2% | 99% | 0.75 GiB |
| `be_antenna_repair` | 原生天线检查与修复（439 个子进程） | 858.0 | 29.4% | 99% | 0.63 GiB |
| `be_antenna_verify` | 修复谱系审计 | 13.0 | 0.4% | — | — |
| `be_inspect/def/netlist/powered/gds` | 导出 | 8.5 | 0.3% | — | — |
| `be_extract` | **再做一次全量原生提取** | 538.6 | 18.5% | 99% | 0.75 GiB |
| `be_openrcx` | OpenRCX（参考工具） | 9.6 | 0.3% | 93% | 0.25 GiB |
| `be_timing` | OpenSTA + RustSTA 签核时序 | 43.9 | 1.5% | 101% | 0.48 GiB |
| `be_main` | KLayout foundry main DRC | 287.9 | 9.9% | 114% | 1.82 GiB |
| `be_antenna` | KLayout foundry antenna | 168.0 | 5.8% | 99% | 2.16 GiB |
| `be_lvs` | KLayout full-device LVS | 139.8 | 4.8% | 98% | 1.51 GiB |
| `nf_*` | CEC + qualification | 12.9 | 0.4% | 95% | 0.25 GiB |

各大块的合计：

- 原生提取 ≈ 1,081 s（37%）
- 天线修复加审计 871 s（30%）
- 参考签核 649 s（22%）
- 布线 203 s（7%）
- 前端 69 s（2%）

### 2.3 LibreLane run16f 时间分解（`L16f/steps.json`、`L16f/runs/*/*/runtime.txt`）

| 块 | 内容 | Wall (s) |
| --- | --- | ---: |
| `grt-00` | 见下方拆分 | 290.6 |
| GRT ECO / 角点观测 | 时钟保护、TT/SS 各 3 轮 GRT、2 轮 ECO | 195.0 |
| `detailed-0..4` | 5 轮 TritonRoute | 1,976.6 |
| routed timing、ECO、census | | 124.1 |
| main DRC（失败） | 2,790.0 减去 steps 合计 2,587.6 | ≈202 |

- `grt-00` 内部：Yosys 81.4 s，5 次 STA 合计 111.3 s，GPL 4.3+2.7 s，DPL 1.0 s，CTS 9.7 s，GRT 2.5 s，`repair_antennas` 34.2 s。
- 每轮 detailed 里 TritonRoute 本身约 232–237 s，其余是 RCX 2.7 s、STA 24 s、IR 16 s、Magic streamout/writelef 44 s、KLayout XOR 35 s。

### 2.4 由基线得出的判断

1. RustEDA 的瓶颈不在布线算法，在**提取、天线编排和串行调度**。LibreLane 的瓶颈正相反，是反复做 detailed routing。
2. RustROAD 单线程 203 s 就完成了布线，TritonRoute 多线程跑一轮要约 235 s。**RustROAD 有大约 16 倍的 CPU 余量可以花在 QoR 上**，仍然会比对手快。
3. RustSys 综合 1.2 s，Yosys 81.4 s。前端不是瓶颈，别在这里花时间。

---

## 3. 性能 gap：根因与修复（按能省的秒数排序）

### 3.1 天线修复：858 s，目标 ≤ 60 s

**现场事实**（`R16/backend/antenna-repair/lineage.json` 及子进程产物）：

- 驱动脚本是 `P/antenna-v3/source03/native_antenna_groups.py`。每组 128 条 net 调用一次 `rustroad-v24 check-antenna --selected-nets …`，每次都是独立进程，每次都要重新解码整个 workspace。
- 子进程分布：

  | 用途 | 次数 | 合计 | 平均每次 |
  | --- | ---: | ---: | ---: |
  | 轮内检查 | 179 | 279.1 s | 1.56 s |
  | 修复前检查 | 64 | 111.6 s | 1.74 s |
  | 修复 | 64 | 241.7 s | 3.78 s |
  | 最终全量检查 | 128 | 218.9 s | 1.71 s |
  | inventory/model | 4 | 6.3 s | — |

- 每次修复都生成一个全量 revision：rev4 → rev56，`design` 文件每份 37 MB。`backend/postroute/road` 共 5.5 GB。
- 报告合计 `report_bytes` 750,375,943。整个 `antenna-repair` 目录 738 MB。
- 结局：
  - `status: unresolved`，`stop_reason: attempt_budget`；
  - `unresolved_supported_nets` 217 条，`total_jumpers` 95，二极管 52 个；
  - `whole_layout_antenna_status: unchecked`；
  - 同一版图上 foundry antenna deck 报 0 违例。
- 原生模型的 `safety_margin` 是 0.10（比率超过 LEF 限值的 90% 即判违例），推导依据是 sky130 上原生值比 Magic 低 5.1–8.4%。

**根因**：

1. 进程粒度太细。每组都要付一次解码开销，约 1.5 s，439 次。
2. 保守模型加上 attempt 预算，导致大量预算花在假阳性 net 上，跑到预算用完才停。
3. 全量 revision 复制和巨大的 JSON 报告。

**修复**：

- **B3（结果不变）。** 在 RustROAD 里加一个单进程命令，例如 `antenna-repair --plan <json>`。
  - 模型只解码一次；全量检查按 net 用 rayon 并行；每次修复后只重查连通性变化了的 net。
  - 修复的顺序和决策规则完全照搬 `native_antenna_groups.py`：同样的分组、同样的轮次、同样的预算，保证最终 revision 和 jumper/二极管集合逐字节一致。
  - 只在结束时（或每轮结束时）提交一个 revision，保留 `--base-rev` 语义。
  - 报告改成一个紧凑摘要，外加可选的二进制 side-table。
  - 门：最终 `design` 文件 sha256 等于 `f34b3205…`（rev56）；`inventory-final.json` 和 `model-final.json` 的 sha256 与现场一致。
- **C7（会改 QoR，放到 C 阶段）。** 用 foundry antenna deck 当黑盒 oracle 来标定原生模型。
  - 在 `R16` 最终版图（rev56）上，对比"原生判违例的 net 集合"和"foundry deck 判违例的 net 集合"，按层、按比率区间统计假阳性率和假阴性率。
  - 目标：假阴性为 0，同时把假阳性压到可忽略。
  - 标定结果只用来调 margin 和模型细节。签核仍然只认 foundry deck，原生检查永远只是预筛。
  - 少修假阳性的直接好处是 jumper 更少，via 也更少。

### 3.2 原生 RC 提取：2 × 约 540 s，CPU 目标 ≤ 2 × 60 s，GPU 目标 ≤ 2 × 10 s

**现场事实**（`R16/backend/postroute/logs/check0-extract.stdout`）：

| 统计项 | 值 |
| --- | --- |
| nets | 16,329 |
| `wire_runs` | 520,692 |
| vias | 301,669 |
| shapes | 2,735,374 |
| intervals | 17,680,976 |
| `passive_intervals` | 14,937,021 |
| `nonspanning_plane_intervals` | 11,262,521 |
| work | 55,965,971,804（`--max-extract-work` 1e11，已用 56%） |
| `max_run_candidates` | 15,881 |
| 内存估算 | `estimate_bytes` 3,716,407,476 B（3.46 GiB）；实测 RSS 787,208 KiB（0.75 GiB），估算偏保守约 4.6× |

- 命令行没有 `--threads`，CPU 利用率 99%。
- 输出：
  - check0（rev4）的 SPEF 是 `622f36c7…`，60,981,904 B；
  - 最终（rev56）的 SPEF 是 `cd835ce7…`，61,007,657 B。

**根因**（`ROAD/crates/rustroad-core/src/extract.rs`）：

1. `extract_cpu_ref`（约第 1756 行）对每个 run 执行 `for &layer in &routing_layers { bins.visit(layer, query, …) }`，也就是**每个 run 都查全部有 RC 模型的布线层**。
2. 查询窗口是 `extraction_bin`（约第 282 行），取所有层所有 context 表最大距离的 2 倍，所以候选列表最多到 15,881 个。
3. `Bins::visit`（约第 1079 行）是二维 bin 全扫，没有按层、按 track 排序的区间索引。
4. 整个函数是串行的，`stats` 以 `&mut` 贯穿始终。
5. 天线修复只动了约百条 net（95 个 jumper、52 个二极管），却做了一次全量重提。

**修复**（B1/B2 都要求逐字节一致）：

- **B1a 查询剪枝。** 按 (run 所在层, 目标层) 从 `RcTech` 推出真正可能有贡献的层和距离：
  - 同层取侧向窗口；
  - 上下层取 plate/overlap/shield 所需的最近层。
  - 只查这些层。
  - 在 debug/test 模式下断言：剪枝后的候选集合是原候选集合里"实际产生贡献"那部分的超集。
- **B1b 每层排序区间索引。** 用每层按 track 坐标排序的区间索引加 sweep，代替二维 bin 全扫。
- **B1c 并行。**
  - 每个 run 的贡献在 rayon 里独立算成一个小 `Vec`；
  - 再**按 run_index 顺序**串行合并到 net 的 ground/coupling 累加器，这样 f64 加法顺序和现在完全一样；
  - `stats` 每线程各一份，最后做整数求和。
  - 线程数走 `--threads`。
  - 内存估算：每线程候选 scratch 16 MiB × 16 = 256 MiB，加上每个 run 的贡献缓冲（重新估算）。
- **B2 增量提取。**
  - 用"net 自身几何 + context 窗口内邻居几何"的哈希给每条 net 的 RC 结果做键；
  - 天线修复后只重提脏 net 和它的邻居；
  - 门：增量 SPEF 与全量 SPEF **逐字节相同**；
  - 不确定时回退全量，并在 receipt 里写明。
- **B1/B2 的门**：在复制出来的 workspace 上 replay，SPEF sha256 分别等于 `622f36c7…` 和 `cd835ce7…`；wall time 和 peak RSS 写进 commit message。

### 3.3 参考签核串行：649 s，目标 ≤ 320 s

- **事实**：
  - `be_main`、`be_antenna`、`be_lvs`、`be_openrcx`→`be_timing` 只依赖 GDS/DEF/netlist，彼此独立，现在却串行跑；
  - `integration/ibex_sg13g2_ez130/signoff_drc.py` 用的是 `threads=2`、`run_mode=deep`。
- **B4**：在 flow recipe 里把这几个 step 声明成可并发：
  - `be_main` ‖ (`be_antenna` → `be_lvs`) ‖ (`be_openrcx` → `be_timing`)，CPU 集互不重叠；
  - 内存 1.82 + 2.16 + 0.48 ≈ 4.5 GiB（取各自实测 max RSS），在 8 GiB cgroup 内；
  - 预计 wall ≈ max(288, 168+140, 10+44) ≈ 308 s。
  - 门：各报告的违例计数和内容与串行运行一致（时间戳除外）。
- **可选**：KLayout 的线程数是两边 flow 共用的参考 runner 参数。要改，必须两边同时改、写进 receipt，并证明报告完全一致。
- **QoR 改善的副作用**：via 和 shape 减半后，DRC、LVS、提取都会更快，这部分算在 C 阶段的收益里。

### 3.4 布线：203.2 s（`R16/logs/be_route.stderr`）

| 阶段 | 时刻 / 耗时 |
| --- | --- |
| access | 12.1 s 结束 |
| global | 0.3 s |
| detailed | 62.2 s（结束于 74.7 s，71 条 net 失败） |
| repair pass 1 | 114.4 s（180 轮，193 次 rip-up） |
| rule repair pass 2–3、cleanup、validate | 约 11 s |

- `--threads 16`，但 CPU 99%：`troute.rs` 没有任何并行，`routing.rs` 也没有。
- **B 阶段（结果不变）**：pin access 拆成两步。候选生成按 terminal 并行；冲突裁决按原顺序串行。门：DEF sha256 不变。
- **C4（会改结果）**：detailed routing 分批并行。
  - 按 net 顺序贪心组批，同一批内 net 的扩展 guide 框两两不相交；
  - 每批对一个冻结快照并行搜索，**按 net 顺序**提交，冲突的 net 回到队列；
  - 结果确定，但和串行版不同，所以必须过 QoR 门（第 7 节）。
  - 内存：现有估算器给出 search 2,453 MiB / 总计 3,955 MiB，实测峰值只有 1,073 MiB（偏保守 3.7×）。并行前要按"共享只读图 + 每 worker 有界 heap/visited"重写估算。worker 数 × 每 worker 上限必须 ≤ 4 GiB，否则就要拿 scale.lock。

### 3.5 I/O 与 workspace 开销

- run16 的 public-workspace 共 9.9 GB，其中 `backend/postroute/road` 5.5 GB（56 个 revision）、`antenna-repair` 738 MB、`timing` 376 MB。
- **B5**：
  - revision 存储改成内容寻址（相同 side-table 用 hardlink/reflink 或按 chunk 哈希去重）；
  - 报告改成紧凑格式。
  - 收益约几十秒加几 GB 磁盘。门：所有产物哈希不变。

### 3.6 不要优化的部分

- 前端合计 68.8 s，其中 `fe_synth` 1.2 s，RustSTA 三次 `*_run` 每次约 9 s。
- RustSTA 已经有 CUDA timing 核（`modules/RustEDA-Common/crates/core/rusteda-compute/src/timing/kernels/timing.cu`，cudarc + NVRTC）。

---

## 4. QoR gap：根因与修复

### 4.1 面积：差距完全来自 tap

由 `A/cellmix.py` 对两份最终 DEF 计算，总数与发布值逐位吻合。

| 类别 | RustEDA 个数 | RustEDA µm² | LibreLane 个数 | LibreLane µm² | 差 µm² |
| --- | ---: | ---: | ---: | ---: | ---: |
| tap (`WELLTAP` 4.23 µm²) | 13,923 | 58,944 | 6,900 | 29,212 | **+29,733** |
| 时序单元 | 1,998 | 66,472 | 1,905 | 52,216 | +14,256 |
| 组合逻辑 | 9,081 | 78,364 | 10,186 | 97,160 | −18,796 |
| buffer（含时钟） | 3,446 | 22,857 | 3,656 | 31,388 | −8,531 |
| inverter | 1,521 | 6,439 | 331 | 1,407 | +5,032 |
| 天线二极管 | 52 | 147 | 926 | 10,454 | −10,307 |
| tie | 162 | 686 | 162 | 686 | 0 |
| **合计（不含 filler/decap/macro）** | | **233,909** | | **222,522** | **+11,387** |

- **时序加组合合在一起看，RustEDA 小 4,540 µm²。**
  - RustSys 把使能触发器映射成 `DFFMRQX2`（36.69 µm²，`next_state = SE ? SD : D`）。
  - LibreLane 用 `DFFRQX3`（28.22 µm²）加一个独立的 `MUX2X1`（12.70 µm²）。
  - 每个使能触发器省 4.23 µm²。这是 RustSys 的优点，别改坏它。
- **Tap**：
  - `integration/ibex_sg13g2_ez130/contract.json` 写着 `tap_rule_max_distance_um: 20` 和 `native_tap_max_gap_um: 15`；
  - `native-physical.json` 里 `tap.max_gap: 15000`；
  - RustROAD 在**每一行**都按 15 µm 间距插 tap（13,923 / 285 行 ≈ 49 个/行）；
  - LibreLane 的日志显示 `tapcell -distance 15.0`，插了 6,900 个（约每行 24 个，约为 RustEDA 的一半）。
- **T1（需要用户确认规则后才能做）**：
  - 先找到 20 µm 规则的原始出处（IHP SG13G2 layout rules 里的 latch-up / well-tie 距离规则），确认相邻行共用的 N-well/衬底里的 tap 能否计入距离。
  - 能计入的话，改成交错（checkerboard）排布，有效距离 ≤ 20 µm，预计省 ≈ 29,700 µm²。
  - LibreLane 插得少不能当作合规证据。
  - 同时要写一个**独立于插入器**的 tap 覆盖检查器（Rule 8：不能同一套假设既做插入又做验证）：从每个 diffusion 到最近 tap 的几何距离出发，或者用 foundry deck 里的对应规则（如果有）。
- **次要项**：1,521 个 `INVX1`（6.4k µm²）。可以试试反相吸收（改用反相输出的门或 `QN`），约省 5k µm²，优先级低。

### 4.2 线长：布局约占 65%，布线约占 35%

由 `A/hpwl.py` 计算：同一定义（每条信号 net 的 pin 中心包围盒，含时钟，不含电源），两份最终 DEF。

| | RustEDA | LibreLane |
| --- | ---: | ---: |
| 最终 HPWL | 1,300,746 µm（16,329 nets） | 954,776 µm（16,236 nets） |
| 布线线长 | 1,678,599 µm | 1,077,762 µm |
| 布线 / HPWL | **1.290** | **1.129** |

分解：

- LibreLane 的绕行比精确值是 1,077,762.0 / 954,776.1 = 1.12881。RustEDA 如果按这个比值布线，线长应为 1,468,297 µm。超出的 210,302 µm（35%）归**布线**。
- (1,300,746 − 954,776) × 1.12881 = 390,535 µm（65%）归**布局**。两项合计 600,837 µm。

辅助数据：

- RustROAD 布局模式：
  - 临时布局 `analytic_b2b_bisection`，legal HPWL 1,302,310 µm；
  - 最终布局 `seeded_eco_legalize`。`fe_final_place` 报告 1,550,640 µm，布线时 workspace 报告 1,286,432 µm。这两个内部数字的定义未核实，比较时只用上表的同一定义 HPWL。
- LibreLane：GPL 结束时 HPWL 615,152 µm；DPL 后 legalized 879,900 µm；CTS 后 resizer 之后 915,487 µm；GRT 后修复之后 940,917 µm。

### 4.3 Via 与分层：问题在布线器的代价模型

由 `A/layers.py` 计算：layer 随每个 via 切换到另一层，总数与发布值逐位吻合，RustEDA 每层线长与 OpenDB `dbWire` 结果一致。

| 层 | RustEDA µm | LibreLane µm | 差 |
| --- | ---: | ---: | ---: |
| Metal2 | 413,426 | 409,898 | +3,528 |
| Metal3 | 546,281 | 490,863 | +55,418 |
| Metal4 | 358,432 | 78,619 | **+279,813** |
| Metal5 | 281,064 | 97,671 | **+183,393** |
| TopMetal1 | 53,575 | 26 | **+53,549** |
| TopMetal2 | 25,534 | 427 | **+25,107** |

| Cut | RustEDA | LibreLane |
| --- | ---: | ---: |
| Via1 | 58,521 | 58,758 |
| Via2 | 128,777 | 62,521 |
| Via3 | 61,247 | 7,558 |
| Via4 | 48,613 | 1,443 |
| TopVia1 | 4,141 | 4 |
| TopVia2 | 560 | 2 |

**DEF 实例**（`R16/logs/be_def.stdout`，net `rst_ni` 和第一条 net）：

- 有大量 `Via2_XY_so ( x y ) Via2_XY_so ( x' y ) Via2_XY_so …`：M2 和 M3 交替，每段一个 via，也就是阶梯走线，每个台阶都换一次层。
- TopMetal1 上有成串的 420 DBU 小台阶：`( 155610 532140 ) ( 155610 532560 ) ( 156450 532560 ) ( 156450 532980 ) …`。
- 短距离也会一路爬到 TopMetal2 再下来。

**布线器配置**（`integration/ibex_sg13g2_ez130/native-routing.json`，`ROAD/crates/rustroad-core/src/troute.rs`）：

| 参数 | 值 | 说明 |
| --- | --- | --- |
| `access_layer` | `"Metal3"` | 每个 terminal 都爬到 M3（terminal 共 59,021 个），可能是 Via2 多出约 66k 个的主要来源（未单独测量） |
| `via_cost` | 840 | 在 `grid_step` 420 的尺度上，1 个 via 只相当于 0.84 µm 导线，爬 6 层来回 12 个 via 才相当于约 10 µm |
| `heuristic_weight` | 4 | weighted A*：在 Manhattan 启发式下偏好"每一步都离目标更近"，在多条等长路径里倾向交替方向，于是形成阶梯 |
| `repair_heuristic_weight` | 2 | |
| `wrong_way_factor` | 4 | |
| `capacity_percent` | 80 | |
| `gcell` | 13,440（13.44 µm） | |
| `global_iterations` | 20 | |

- 代价里**没有拐弯代价**：`troute.rs` 约第 3054–3066 行和约第 3388 行只有 `via_cost`、`wrong`，以及 `SINGLE_OWNER_VIAS`/`NEW_OWNER_VIAS` 这类倍数。
- **层代价不随层高变化**，global routing 也没有考虑 via stack 的层分配。

### 4.4 QoR 修复清单（C 阶段，都会改结果，都要过第 7 节的 QoR 门）

按"预期收益 / 风险"排序。每一项都先在第 7.1 节的 route replay 上做 A/B，再做完整 flow。

| ID | 改动 | 位置 | 预期 |
| --- | --- | --- | --- |
| C1 | A* 加拐弯代价（同层改方向，以及"经 via 拐弯"），等价路径按"直行优先"打破平局；主搜索的 `heuristic_weight` 降到 1–1.5（weight 只留给 repair） | `troute.rs` `route_net`（约第 3008 行）和代价计算（约第 3054–3066、3388 行） | via 大幅减少，绕行比下降；搜索量上升，用 C4 并行补回 |
| C2 | 布线后拉直：对每段两端点子路径尝试 L/Z 形重布，DRC 干净且更短或 via 更少才替换；按 net 顺序，结果确定 | `routing.rs` cleanup 之后 | via −20~40%（估算） |
| C3 | 层代价：每层每单位长度的代价随层高递增，短 net 惩罚上层；global routing 做带 via stack 代价的 3D 层分配，结果作为 guide | `troute.rs` `global`（约第 2455 行） | M4/M5/TM 线长大幅回落 |
| C4 | detailed routing 分批并行（第 3.4 节） | `troute.rs` | 抵消 C1 的搜索开销 |
| C5 | `access_layer` 从 Metal3 改为 Metal2（M1 pin → Via1 → M2），保留 M3 作为回退 | `native-routing.json`；pin access 的规则检查 | Via2 −40k~−59k（估算） |
| C6 | `via_cost` 扫 840 / 1,680 / 3,360 / 5,040，和 C1、C3 联合调 | `native-routing.json` | 找 WL 与 via 的折中点 |
| P1 | 全局布局升级：按公开论文从零实现 ePlace/Nesterov 电场密度布局（`cpu_ref` f64，确定性）；B2B+二分保留作初值 | `ROAD/crates/rustroad-core/src/place.rs` | HPWL 目标 ≤ 1.0M µm |
| P2 | 布局在 tap 和 tap 之间的空段内合法化导致碎片化（`place.rs` 文档第 9–11 行）；配合 T1 减少 tap，并检查 legalizer 的最大位移和 HPWL 增量 | `place.rs` | |
| P3 | 最终布局是 `seeded_eco_legalize`（从临时布局继承），ECO 后再做一轮增量全局优化，而不是只做合法化 | 前端 recipe 和 `place.rs` | |
| T1 | tap 交错排布（第 4.1 节，先确认规则） | `native-physical.json` 和 tap 插入代码 | −29.7k µm² |
| C7 | 天线模型标定（第 3.1 节） | `rustroad-workspace/src/antenna.rs` 及驱动 | jumper/via 更少，修复更快 |

Ibex 上的 QoR 目标（同一 floorplan、同一约束）：

- 布线线长 ≤ 1,150,000 µm（stretch ≤ 1,080,000）；
- via ≤ 160,000（stretch ≤ 135,000）；
- HPWL ≤ 1,000,000 µm；
- 标准单元面积 ≤ 215,000 µm²（取决于 T1）；
- 时序两角都为正，density ≤ 70%，16/16 gate 全过。

---

## 5. NVIDIA cuda-rust：机会与做法

### 5.1 cuda-rust 是什么（2026-10-30 从 GitHub 和文档站读取）

- 仓库：`https://github.com/NVIDIA/cuda-rust`，Apache-2.0。官方定位是"NVIDIA's CUDA platform for Rust"，状态写的是 early-stage research project，README 原文 "Expect bugs, incomplete features, and API breakage"。cuda-oxide 的版本是 v0.1.0，标注 early-stage alpha。
- 组成：
  - host 运行时：`cuda-bindings`（driver API，用 bindgen，构建时需要 `cuda.h`）、`cuda-core`（context/stream/内存/module/launch）、`cuda-async`（lazy 操作、stream、graph）；
  - 两个 kernel 模型：
    - **cuda-oxide（SIMT）**：自定义 rustc codegen backend，AOT 把 Rust 编成 PTX，用 `cargo oxide` 构建；
    - **cutile-rs（Tile）**：首次 launch 时 JIT，经 Tile IR 编成 cubin，面向张量和稠密 tile 运算。
- cuda-oxide 的要求：
  - Linux（测试平台 Ubuntu 24.04），**sm_80+**，driver 580+，CUDA Toolkit 13.0+（`nvcc`、`cuda.h`、`curand.h`），带 NVPTX 后端的 LLVM 21+；
  - Rust **1.99.1**（由它自己的 `rust-toolchain.toml` 固定，需要 `rustc-dev` 和 `llvm-tools`）。
- cuda-oxide 的能力（`appendix/supported-features.html`）：
  - 支持泛型、const generic、闭包、slice、`core` 里的 `Option`/`Iterator`；
  - **不支持** `dyn`、`Box`/`Vec`、`std`、panic（命中即 trap）；
  - 原子操作覆盖 U32/I32/U64/I64/F16/F32/F64，作用域有 device/block/system；
  - warp 的 shuffle/vote/match，sm_80+ 上的整数 `redux.sync`；
  - `sync_threads`，cooperative launch（`Grid::sync`），静态和动态 shared memory；
  - TMA、cluster、DSMEM 需要 sm_90+，**本机 sm_89 不可用**。
- cutile-rs：
  - sm_89 需要 Tile IR 13.2（CUDA 13.2+ 的 `tileiras`），Rust ≥ 1.89；
  - Tile 模型面向稠密张量，EDA 里不规则的几何和图计算不适合，**本计划不用 cutile-rs**。

### 5.2 本机适配

- GPU：NVIDIA GeForce RTX 4090 Laptop，16,376 MiB，compute capability 8.9，driver 615.78.08。满足 cuda-oxide 的 sm_80+ 和 driver 580+。
- 没有系统级 CUDA Toolkit（没有 `nvcc`）。系统 LLVM 是 23.1.3，带 `nvptx64`。主仓库工具链是 1.97.1。
- RustSpice 的做法证明可以**不装系统 CUDA**：
  - 用 NVIDIA PyPI wheel（`nvidia-cuda-nvcc`、`-runtime`、`-crt`、`nvvm`、`nvjitlink`，13.4）加系统 `llc`；
  - cuda-rust 固定在 revision `a4a939a9b5f01f3bf712e92386922e1bc7c434a9`；
  - 步骤见 `modules/RustSpice/crates/rustspice-backend-cuda-simt/kernels/README.md`。
- 注意：GeForce 的 FP64 吞吐只有 FP32 的 1/64（RustSpice 实测：kernel 受访存和 FP64 限制）。EDA 核要把重活放在**整数几何**上。

### 5.3 仓库里已有的先例：RustSpice batched DC

详见 HANDOFF.md 2026-10-28 两节，以及 `modules/RustSpice` 的 `15b6caf`、`41d66a1`。

- `kernels/batch_newton.rs` 是**同一份 Rust 源码编译两次**：
  - 在普通构建里 `include!` 成 rayon `cpu_ref`；
  - 经 cuda-oxide 编成 `batch_newton.ptx`。PTX 提交进仓库，host 用 `include_str!` 嵌入，用 `libloading` 调 driver API（`cuModuleLoadDataEx`）。
  - 所以构建不需要 CUDA，也不需要 cuda-oxide；运行只要有 `libcuda` 就行。
- 构建用 `--no-fmad`：只有显式的 `mul_add` 会融合，CPU 和 GPU 的舍入一致。PTX 字节可复现。
- 结果：
  - GPU 与 `cpu_ref` 的差 ≤ 5.6e-16（只有 libdevice 与 libm 的 `exp`/`ln` 在末位不同），MOSFET 负载上逐位一致；
  - 1M corner 时 2.89×；**≤ 1,024 corner 时 CPU 更快**；
  - GPU 初始化（driver、context、PTX JIT）每个进程约 0.3 s。
- 经验：
  - 笔记本 GPU 计时漂移 15–20%，A/B 要交错重复跑；
  - `cargo test` 不会重建 CLI 二进制。

### 5.4 推荐的集成架构

1. **核放在 `rusteda-compute`**（`modules/RustEDA-Common/crates/core/rusteda-compute`）。Rule 5 要求加速任务都在这里。它已经有 task trait、权威的 CPU backend、GPU 自检和 `dispatch`，现有任务是 `cut` 和 `timing`。
   - RustROAD 目前**不依赖** rusteda-compute，`Cargo.toml` 里只有 rayon/serde/slotmap 等，而且 `unsafe_code = "deny"`。
   - 新增依赖的方式照 RustSys/RustSTA：用 git rev 依赖 `lab-emi/RustEDA-Common`。unsafe 留在 rusteda-compute 的 `cuda.rs` 里，那里已经有 `#![allow(unsafe_code)]`。
2. **设备代码用 cuda-oxide 写成 Rust，和 `cpu_ref` 共用同一份源码。**
   - 放在 `rusteda-compute/src/<task>/kernels/<task>.rs`，另建一个独立的 `gpu/` 小 crate，带自己的 `rust-toolchain.toml`（1.99.1），用 `cargo oxide` 产出 PTX；
   - PTX、它的 sha256、源码 sha256 和 cuda-rust revision 一起提交；
   - 加一个 CI/脚本检查：源码哈希变了，PTX 就必须重建。
3. **host 端沿用 rusteda-compute 现有的 cudarc 0.19.10。** 它已经能加载 NVRTC 产出的 PTX，加载预编译 PTX 是同一条路径，不需要再引入 alpha 版的 `cuda-core`（后者构建时要 `cuda.h` 和 bindgen）。如果 cudarc 的加载 API 不合适，就照 RustSpice 用 `libloading`。
4. **设计原则："GPU 算整数几何和拓扑，CPU 按原顺序做 f64 数值累加"。** 这样 QoR 由构造保证不变。门用产物哈希：SPEF、DEF 逐字节一致，而不是容差。
5. 构建加 `--no-fmad`；没有 GPU、自检失败或预算不足时，静默回退到 `cpu_ref`，并在 receipt 里写明实际用了哪个 backend。
6. **需要用户决定**：AGENTS.md Rule 5.2 原文是 "Linux GPU kernels use native CUDA (`cudarc`, `.cu`, `cuFFT`, `cuSPARSE`)"。cuda-oxide 产出的 PTX 走的同样是原生 driver，RustSpice 已经在用，但 RustROAD 和 RustEDA-Common 引入它之前，请用户明确同意，或者修订 Rule 5 的措辞。

### 5.5 候选核（按"Ibex 上能省的秒数 × QoR 安全性"排序）

| ID | 核 | 前置条件 | 在 GPU 上算什么 | QoR 保证 | Ibex 收益（估算） | 规模化收益 |
| --- | --- | --- | --- | --- | --- | --- |
| G1 | RC 提取的几何阶段 | B1 已把 `extract_cpu_ref` 拆成"几何 → 数值"两阶段，并且逐字节一致 | 每个 run 的候选收集、区间裁剪（`subtract`/`uncovered`/`face_portions`）、重叠长度和间距，全是 i64 DBU；产出按 (run, side) 排序的区间表（约 1,770 万条 × 约 24 B ≈ 425 MB，PCIe 传输约 20 ms） | f64 采样和累加仍在 CPU 上按原顺序做，SPEF 逐字节一致 | 每次 60 s → 约 10 s，两次合计省约 100 s | 高：work 随 shape 数超线性增长，OpenC910 级规模上 work 预算本来就会触顶 |
| G2 | 电场密度全局布局 | P1 的 `cpu_ref` 已经合入并过 QoR 门 | 密度散射（i64 定点原子，Rule 6.3）、DCT/Poisson（cuFFT，经 cudarc，或自写）、WA 线长梯度（按 net 分段规约）、Nesterov 更新 | 确定性模式下与 `cpu_ref` 一致。做不到逐位一致时，要通过统计门：3 个种子下 HPWL 偏差 ≤ ±0.5%，最终 flow QoR 不劣于 `cpu_ref` | Ibex 约 24k 个可动单元，CPU 估计 10–30 s → GPU 2–5 s | 高：百万单元级是数量级收益 |
| G3 | global routing 的拥塞图和 pattern route 评估 | C3 已完成 | 每条 net 的 L/Z/3D 候选代价批量评估；按 net 顺序在 CPU 上提交 | 提交顺序确定，结果和 CPU 版一致 | Ibex 很小，可以忽略 | 中 |
| G4 | 天线逐 net 面积 DP | — | 不做。B3 进程内并行就够 | — | — | 低 |
| G5 | STA | — | 已经有 `timing.cu`（cudarc/NVRTC），Ibex 上不是瓶颈，不动 | — | — | — |

**不该上 GPU 的**：

- detailed routing 的 A*（不规则、强依赖、每次 expand 都要查 Spatial）：用 C4 的 CPU 并行。
- 黑盒参考工具（KLayout/OpenRCX/OpenSTA）：不是我们的代码。
- RustSys 综合：已经 1.2 s，`cut` 已经有 CUDA 核。

**对 Ibex 的诚实预期**：GPU 合计省约 100–150 s。真正的大头是 B 阶段的去串行（约 2,000 s）。GPU 的价值主要在更大的设计上，所以 G1/G2 要配一个更大的规模 fixture（比如已有的 OpenC910 相关输入）来展示收益，并按 Rule 8.3 报告**端到端** wall time（含 PTX JIT、H2D/D2H、同步），不能只报 kernel 时间。

### 5.6 Benchmark 公平性

- A2A 的主比较维持 **CPU-only**：同样 16 CPU、8 GiB。
- 如果 RustEDA 用了 GPU，要另外发布一行 CPU+GPU 结果，并在 receipt 里记录 GPU 型号、driver、cuda-rust revision 和 PTX 哈希。
- LibreLane 那边 OpenROAD 的构建带 `+GPU` feature（见它的日志头），但默认配置下没启用。不能声称它"无法使用 GPU"。

### 5.7 风险

| 风险 | 缓解 |
| --- | --- |
| cuda-oxide 是 alpha，API 会变 | 固定 revision，PTX 提交进仓库，构建不依赖它 |
| Rust 1.99.1 和 `rustc-dev` | 只在 `gpu/` 小 crate 里用 |
| PTX 符号名可能被 mangle | 先做 D0 spike 确认，参考 RustSpice 的 `kernels/gpu/` |
| 显存不受 cgroup `MemoryMax` 约束；CUDA 会保留虚拟地址空间 | 用 `systemd-run -p MemoryMax`，不要用 `ulimit -v`（AGENTS Rule 9.3） |
| GPU 初始化 0.3 s | 每个进程只初始化一次；小 batch 走 CPU（阈值实测确定） |

---

## 6. 路线图

| 阶段 | 内容 | QoR | 端到端目标（估算） | 门 |
| --- | --- | --- | --- | --- |
| A | 测量基建：checkpoint replay、QoR 脚本、可复现性确认 | 不变 | — | 用 run16 原二进制 replay 时产物哈希一致 |
| B | B1 提取剪枝和并行、B2 增量提取、B3 天线单进程、B4 签核并发、B5 revision 去重；可选 pin access 并行 | **逐字节不变** | ≤ 900 s | DEF/GDS/SPEF/netlist 的 sha256 与 run16 相同，16/16 |
| C | C1–C7、P1–P3、T1（T1 要等用户确认规则） | 变好 | ≤ 750 s | 第 7.3 节的 QoR 门、dual-PDK 回归、16/16 |
| D | D0 工具链 spike（可以和 B 并行做），然后 G1，再 G2；G3 可选 | 不变（相对 C 的 `cpu_ref`） | ≤ 650 s（GPU）；同时报告 CPU-only | GPU 与 `cpu_ref` 产物哈希一致；端到端 wall time |
| E | 全新公开 run，重新做 A2A | — | — | **先征得用户同意**才发布、才 push |

LibreLane 参照：2,790.0 s 时失败；线长 1,077,762 µm，via 130,286，面积 222,522 µm²。

先做 B：它把每轮完整 flow 从约 49 分钟降到约 15 分钟，C 阶段的迭代会快 3 倍以上。

---

## 7. 测量与验收方法

### 7.1 单步 replay（A 阶段，所有 B/C 迭代都靠它）

- 每个 step 的完整命令行在 `R16/logs/<step>.command.json`（`argv` 字段），输入在 `R16/backend/*-inputs.json`。
- 把要用的 workspace **复制**到 `ROOT/.work/perf-<日期>/replay/`，必须保证不会写回原处。然后把二进制换成新的再跑。
- `postroute/road` 有 5.5 GB（56 个 revision），复制前先看磁盘余量。它的 HEAD 是 rev56，所以下面的例子对应最终 SPEF（`cd835ce7…`）。要复现 rev4 的 `622f36c7…`，得先确认 CLI 能不能指定 revision，不能就只用最终那份做门。例如：

```bash
ROOT=/home/cgao/git/RustEDA; R16=$ROOT/.work/ibex-sg13g2-bringup-20261028/run16/public-workspace/steps/rtl2gds/attempt1/output; W=$ROOT/.work/perf-20261031/replay
mkdir -p $W && cp -a $R16/backend/postroute/road $W/road-final && cp -a $R16/backend/profiles $W/profiles
systemd-run --user --scope -p MemoryMax=8G -p MemorySwapMax=2G -p AllowedCPUs=24-31 \
  timeout 1800 /usr/bin/time -v -o $W/extract.time <新 rustroad> --workspace $W/road-final --threads 8 \
  --max-expansions 8000000 extract --rc-profile $W/profiles/ihp-rc-nom.json --corner nom \
  --out $W/native.nom.spef <其余参数照抄 R16/logs/be_extract.command.json>
sha256sum $W/native.nom.spef   # 必须等于 cd835ce76826f6575cc84fe69602ca7089f5c007565ed2b4de5e98a1cf74d9e8
```

- 在 replay 之前，先用 `P/bin/rustroad-v24` 跑一次同样的 replay，确认哈希能复现。复现不了，就**先找出不确定性来源**，再做别的。
- 布线 replay 的输入是 `R16/frontend/road-final`（`--base-rev rev3`），配置是 `R16/frontend/physical/routing.json`。比较时看 stdout 里的 `routing_segments`、`routing_expansions`、`route_memory`，以及导出 DEF 后用 `A/layers.py` 和 `A/hpwl.py` 算出的结果。
- 天线 replay 的输入是 `R16/backend/antenna-inputs.json` 和复制出来的 `postroute/road`（rev4）。

### 7.2 QoR 脚本（`A/`，都是只读、流式解析）

```bash
python3 -I $A/cellmix.py <cell.lef> <final.def> [<other.def>]     # 按类别统计面积；合计必须等于 final_density.py 的非 filler 面积
python3 -I $A/hpwl.py <cell.lef>,<sram.lef> <final.def>           # 同一定义的 HPWL
python3 -I $A/layers.py <final.def>                               # 每层线长和每个 cut 层的 via 数；总数必须等于 compute_a2a 的结果
```

- 其中 `<cell.lef>` 用 `/home/cgao/pdks/ez130_8t_v1p1/lef/ez130_8t.lef`，`<sram.lef>` 用 `/home/cgao/pdks/ihp-open-pdk/ihp-sg13g2/libs.ref/sg13g2_sram/lef/RM_IHPSG13_1P_1024x32_c2_bm_bist.lef`。
- 完整 flow 的发布口径仍然是 `ROOT/.work/ibex-closure-a2a-20261029/a2a16/compute_a2a.py` 和 `publish_receipt.py`。

### 7.3 QoR 门（C/D 阶段每一项）

1. 完整的全新 run（照 `P/prepare_run16.py` 和 `P/launch_run.py` 新建 runNN）：16/16 gate、foundry DRC 0、antenna 0、LVS 通过、CEC proven、density ≤ 70%，RustSTA 与 OpenSTA 的差 ≤ 1 ps。
2. 与当前最好的 run 相比：
   - 线长、via、HPWL、面积都不能变差；
   - TT/SS 的 setup/hold WS 必须为正（硬门）；hold WS 比当前最好 run 低的话，要单独解释原因；
   - 时间变差的话，必须给出 QoR 收益来解释。
3. RustROAD 引擎改动要跑 dual-PDK 布线回归：`ROOT/.work/ibex-closure-a2a-20261029/qualification-support/dual_pdk.py --binary <rustroad> --out <新目录>`（sky130 + gf180）。Rule 7.2 涉及 Liberty/映射/STA 时，两个 PDK 的相关测试都要跑。
4. Rule 8：布线、布局的改动不能拿自己写的检查当唯一 oracle。最终裁判是 KLayout foundry deck、OpenRCX+OpenSTA 和 CEC。
5. 性能改动的 commit message 写：端到端 wall time、受影响 step 的 wall time 和 peak RSS、产物哈希，以及 QoR 表。

### 7.4 构建

- `./dev build|check|test|clippy <Module>` 只针对 `modules/<Module>` 签出，而那里有别人的脏工作（见第 8 节）。
- 在独立 worktree 里用有界 cargo：

```bash
systemd-run --user --scope -p MemoryMax=12G -p MemorySwapMax=2G timeout 2400 \
  env CARGO_TARGET_DIR=$ROOT/.work/perf-20261031/target CARGO_BUILD_JOBS=8 RUST_TEST_THREADS=4 \
  cargo build --release --locked -p rustroad-cli
```

- 动手之前先 `free -h`。新二进制按 sha256 存到 `ROOT/.work/perf-<日期>/bin/<sha256>/rustroad`。

---

## 8. 工作方式

1. **开工前**：读 `ROOT/HANDOFF.md`（重点是 2026-10-28 RustSpice cuda-oxide 两节，和 2026-10-30 的 Ibex A2A 发布）、`docs/modules.md`、`ROOT/.work/ibex-sg13g2-bringup-20261028/CURRENT_STATE.json`，以及本文。
2. **签出**：
   - `modules/RustROAD`（`codex/openc910-native-scale` @ `05bcf92`）、`modules/RustROAD-bringup`、`modules/RustROAD-codex-closure` 都有别人的未提交工作；
   - `modules/RustROAD-codex-cycle` 是别人的工作树；
   - **都不要动**。
   - 新建自己的 worktree：

```bash
git -C /home/cgao/git/RustEDA/modules/RustROAD worktree add -b codex/ibex-perf-qor /home/cgao/git/RustEDA/modules/RustROAD-perf claude/ibex-closure-v21
```

   - RustEDA-Common（GPU 部分）同样新开 worktree，基于 gitlink `778fa8d`。
3. **每一步一个 commit**，B 阶段的每项单独可回退。B 阶段的 commit 不得改变产物哈希；C 阶段的 commit 各自附带 QoR 表。
4. **内存估算写在代码注释或 commit 里**（Rule 9.1）。下面是已知的实测值，可以直接当估算基线：

   | 环节 | 实测 |
   | --- | --- |
   | 布线 | 峰值 1.05 GiB（估算器 3.86 GiB） |
   | 提取 | RSS 0.75 GiB（估算器 3.46 GiB） |
   | 天线子进程 | ≤ 0.63 GiB |
   | KLayout main / antenna / LVS | 1.82 / 2.16 / 1.51 GiB |

5. **记录教训**：踩到的坑写进 `ROOT/HANDOFF.md`（cross-repo 的写 root，模块内的写模块自己的 HANDOFF）。`CURRENT_STATE.json` 保持更新。
6. **需要停下来问用户的**：
   - T1 tap 规则的解释；
   - Rule 5 是否允许 cuda-oxide；
   - 任何 push 和对外发布；
   - 删除任何 `.work` 产物（只有用户明确批准的才能删）。

---

## 9. 不要做的事（以前的坑）

- 不要只跑通单测就宣布成功。B 阶段看产物哈希，C 阶段看完整 flow 的 16/16 和 QoR 门（Rule 8）。
- 不要为了提速放宽任何预算或容差：1 ps parity、density 70%、foundry deck 都不动。`--max-*` 预算可以因为算法变了重新推导，但不能因为"超了"就调大。
- 不要在 `R16`、`L16f` 里写任何东西，不要复用它们的 workspace 做 mutating 命令。
- 不要读或引用 OpenROAD/TritonRoute/OpenRCX/KLayout 的源码来"学"它们怎么做，只能看日志和输出。
- 不要把原生天线检查或原生提取当签核。它们只是预筛和内部 repair 用的。
- 不要在笔记本 GPU 上只跑一次就下结论：计时会漂移 15–20%，要交错 A/B 并重复。
- 不要只报 kernel 时间：端到端要包括 PTX JIT、拷贝和同步。
- 不要在 detailed routing 上用 GPU，也不要用 cutile-rs 做几何。
- 不要把 run16 的 2.16 GiB 峰值归到 RustEDA 引擎上，它是 KLayout antenna deck 的。

---

## 附录 A：数据来源

| 内容 | 路径 |
| --- | --- |
| RustEDA step 计时 | `R16/step-receipts.json`，`R16/logs/<step>.time`（GNU time） |
| 布线日志和统计 | `R16/logs/be_route.stderr`，`R16/logs/be_route.stdout` |
| 提取统计 | `R16/backend/postroute/logs/check0-extract.stdout` |
| 天线谱系 | `R16/backend/antenna-repair/lineage.json` |
| RustEDA 最终 DEF | `R16/logs/be_def.stdout`（sha256 `84ecfede…`） |
| 发布产物哈希 | `R16/summary.json` → `artifacts` |
| 布线配置 | `ROOT/integration/ibex_sg13g2_ez130/native-routing.json`；运行时副本 `R16/frontend/physical/routing.json` |
| tap 和物理配置 | `ROOT/integration/ibex_sg13g2_ez130/native-physical.json`，`contract.json` |
| LibreLane 计时 | `L16f/steps.json`，`L16f/runs/*/NN-*/runtime.txt` |
| LibreLane 指标 | `L16f/runs/detailed-4/final/metrics.json` |
| LibreLane 最终 DEF | `L16f/runs/detailed-4/final/def/ibex_sram_bench.def` |
| A2A 测量 | `ROOT/.work/ibex-closure-a2a-20261029/a2a16/a2a-measurement.json`；线长解析器交叉验证见同目录 `wirelength-crosscheck/` |
| cuda-oxide 先例 | `modules/RustSpice/crates/rustspice-backend-cuda-simt/`（`kernels/README.md`、`kernels/batch_newton.rs`、`kernels/gpu/`、`src/driver.rs`、`src/batch.rs`） |
| rusteda-compute | `modules/RustEDA-Common/crates/core/rusteda-compute/src/{lib.rs,cuda.rs,cut/,timing/}` |
| RustROAD 关键源码（`ROAD`） | `crates/rustroad-core/src/{troute.rs,extract.rs,place.rs,routing.rs,route_rules.rs}`，`crates/rustroad-workspace/src/{antenna.rs,extract.rs,routing.rs,physical.rs}` |

## 附录 B：cuda-rust 来源

- `https://github.com/NVIDIA/cuda-rust`（README、目录结构）
- `https://nvidia.github.io/cuda-rust/`（总览）
- `https://nvidia.github.io/cuda-rust/cuda-oxide/getting-started/installation.html`（要求）
- `https://nvidia.github.io/cuda-rust/cuda-oxide/appendix/supported-features.html`（能力矩阵）
- `https://nvidia.github.io/cuda-rust/cutile/latest/reference/compatibility.html`（Tile IR 架构兼容性）
