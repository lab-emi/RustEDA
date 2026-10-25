# RustEDA 系列状态总结（2026-10-25）

范围：RustSys（综合）、RustSTA（时序）、RustROAD（物理设计）、RustLane（流程编排）、RustEDA-Studio（GUI）。本文记录三类内容：已经完成的、尚未完成的、需要独立第三方审查的。

> 一句话结论：在 SKY130 与 GF180 上，**16 个“设计 × PDK”组合中有 11 个完整通过统一 RTL-to-GDS 门禁**。门禁覆盖 DRC、LVS、天线、三角 tt/ss/ff 双引擎双 SPEF 时序、CEC、协同仿真、确定性重建与制品绑定。另外 5 个组合各只剩一项失败。所有工作均为**本地提交，尚未推送**。

---

## 1. 仓库与分支现状

| 仓库 | 远端 main | 本工作主要分支（本地） | 状态 |
|---|---|---|---|
| RustROAD | `625b5f4`（仅 LICENSE） | `feat/rtl2gds` @ `9e85069`（117 commits，v0.5.0） | 未推送；`.work/wt/*` 下有 20 个 worktree |
| RustSTA | `2d8180f`（初始提交） | `feat/signoff-checks` @ `455c43c`（0.4.3 + 文档） | 未推送；`feat/rc-waveforms`（0.3）、`feat/shared-convergence` 为中间分支 |
| RustSys | `c5ec23c` | `feat/shared-convergence` @ `d36d922` | 未推送；`feat/vivado-zynq-ibex` 不属于本工作，未触碰 |
| RustLane | `6c4824a` | 未改动 | 有另一位代理的未提交工作 `feat/postroute-signoff`，按决定未触碰 |
| RustEDA-Studio | 无提交 | 未改动 | 25 个未跟踪文件，与 RTL-to-GDS 无关 |

固定（pinned）工具：
- RustSTA 0.4.3：`RustROAD/.work/sta-signoff/bin/`，`ruststa` sha256 `472de8e8…`，`rusteda` sha256 `c39dbde4…`。
- RustSys 生产者：`RustROAD/.work/tools/rustsys-target/release/rustsys`，sha256 `3de13b3a…`，构建自 `c5ec23c`。
- 签核容器：podman 镜像 `4c48415d8bd0`，含 Magic 8.3.678、Netgen 1.5.323、KLayout 0.30.9。OpenROAD/OpenRCX 使用镜像 `f152917ebc45`，OpenSTA 版本为 3.1.0。

---

## 2. 已完成

### 2.1 端到端结果（RustROAD `feat/rtl2gds`，证据 `docs/evidence/rtl2gds-final2.json`）

| 设计 | SKY130 | GF180 |
|---|---|---|
| pipeline、dense、counter、uart_tx | 通过 | 通过 |
| fifo | 仅 `timing_accuracy` 失败 | 通过 |
| alu | 通过 | 仅 `drv` 失败（3 条网） |
| regfile、mult | 仅 `timing_accuracy` 失败 | 仅 `drv` 失败（8 / 12 条网） |

以下各项在全部 16 行上都通过：
- 原生 GDS 的 XOR = 0；Magic DRC = 0；KLayout DRC = 0；天线 deck = 0。
- Netgen 与 KLayout 两个 LVS 都匹配。
- RTL 等价（版图前、最终网表、带电源网表）与 4096 周期协同仿真。
- 11 个注入式负控制，每个都被对应的 oracle 拒绝。
- setup（tt/ss）与 hold（tt/ss/ff，余量 ≥ +0.05 ns），在 RustSTA 与 OpenSTA 两个引擎、原生 SPEF 与 OpenRCX SPEF 两种来源上均闭合。
- 第二次完整构建字节一致，制品绑定复核通过。
- 从 `git archive` 干净重建，counter、fifo 在两个 PDK 上复跑，得到相同结论。

### 2.2 RustROAD（物理设计，v0.5.0 → feat/rtl2gds）

- **原生 GDSII 导出**：保持层次 SREF，按 PDK 层映射；GF180 坐标精确除 2，奇数坐标直接报错。输出字节确定，与 KLayout DEF→GDS 参考的 XOR 为 0（几何与标签均一致）。
- **布线器**：
  - 保留 LEF 中的 AREA、MINSTEP、ENCLOSURE；支持过孔变体；建立 pin-access 类；同网 notch 与 min-area 修补（patches）。
  - 新增轨道（track）布线器：GCell 全局布线 + 协商拥塞，再做详细布线。
  - 布线存储压缩；DEF 按共线段合并写出，路径数约从 5000 降到 100。
- **布局**：floorplan 命令、解析式布局器、时序驱动的网权重、基于种子的 ECO 合法化。
- **原生 RC 提取**：从 PDK 数据生成 min/nom/max RC profile；建模过孔焊盘、线端延伸与跨越电容；输出 SPEF，RustSTA 与 OpenSTA 均可读入。
- **ECO**：
  - 网表 ECO 原语、`hold-eco`（路径感知，并把报告绑定到对应的 STA 修订版）、`resize_cell`、`buffer_sinks`、端口缓冲。
  - 原生 `check-antenna`，以及用布线层跳线（jumper）完成的天线修复，取代 `diode_2`。
- **闭合循环**：DRV/setup → hold → 天线，迭代次数有明确上限；使用 RustSTA `whatif` 精确评估尺寸调整。
- **签核框架**：
  - 固定工具与 PDK 清单，受限容器运行，以及 Magic/KLayout DRC、天线、密度、Netgen/KLayout LVS、XOR。
  - 共 23 个负控制，已堵住多个 LVS 假通过漏洞：多余端口、标签别名、子单元冒充顶层、OOM 等。
- **统一门禁** `scripts/rtl2gds.py`：
  - 设计 manifest 为 schema v3。阈值固化在代码中，manifest 不能放宽阈值。
  - 包含 11 个负控制、确定性重建与制品哈希绑定，并统计每一步的 wall time 与峰值 RSS。

### 2.3 RustSTA（时序，0.3.0 → 0.4.3）

- **签核检查**：最小脉宽（MPW）、最小周期；DRV（max_transition / max_capacitance / max_fanout）；按角绑定多个 SPEF；`--require-wire-model`；`hold_fix` 报告。
- **延迟计算**：
  - lumped-pi 规则：Rpi < 1e-3·Rd 时按集总负载处理，Rd 取前向 1 fF 处。
  - 一步 Newton 有效电容；线网用驱动端波形驱动。
  - 结果：与 OpenSTA 3.1.0 的最大端点差从 5.21 ps 降到 ≤ 0.97 ps（96 组对比）。
  - 晶体管级 Ngspice 对比：sink 到达时间平均误差从 6.5 ps 降到 0.64 ps。
- **非树 RC 网络**：
  - 电阻环用 Woodbury + Cholesky 精确求解。
  - 断开分量以零电阻并入，并发出警告。
  - 对 Ngspice 的误差 ≤ 0.19 ps（OpenSTA 为 4–29 ps）。
- 修复了一批行为缺陷，包括 inspect 的 required 字段对调、`--kind` 不生效、update 与 run 在 SPEF 下不一致、case analysis 等。
- **测试与工程**：784 测试通过；CPU/CUDA 结果逐位一致；差分 harness 868 个门禁、0 个未解释失败。`ruststa run` 提速 2.3–3.1×。

### 2.4 RustSys（综合）与共享 crate 收敛

- 与 RustSTA 共享 209 个文件，逐字节一致：两边 `xtask shared diff` 都干净，manifest 相同。
- RustSys 的 841 个测试通过；283/283 综合用例输出与固定生产者完全一致（含 SERV/PicoRV32），等价检查 282/282。
- RustROAD 的 cell policy 来自 PDK 排除列表，外加 GF180 的 DRV dont_use（由 Liberty 数据生成）。

### 2.5 已记录的负责人决定（`RustROAD/.work/notes/owner-decisions.md`）

1. 块级签核；IR/EM/SI/OCV/CPPR 标注为 unchecked；密度单独报告。
2. 时钟周期 = max(10 ns, ⌈1.25 × 版图前 ss 关键路径⌉)，在任何物理步骤之前推导并固化哈希。
   - GF180 结果：counter 13、uart_tx 12、fifo 12、alu 18、regfile 12、mult 30 ns。
   - 其余组合保持 10 ns。
3. GF180 保持 `set_max_transition 1.0`；对 ss 下无法达到 1.0 ns 的 10 个单元族设 dont_use。
4. MPW 对比允许“声明偏差”（`deviation_declared`，见 §3.1）。

---

## 3. 尚未完成

### 3.1 门禁剩余项（按优先级）

1. **GF180 DRV**：alu、regfile、mult 有 3–12 条网的转换时间超过 1.0 ns。
   - 根因：大 ECO 缓冲挂在弱驱动后面、hold 延迟单元驱动重负载，且循环在 8 个版图的上限内仍在改进。
   - 该代理被负责人中止。未经验证的 WIP 在分支 `drv-wip`（`a423c28`）与 `feat/rtl2gds-drv`（`f843ecd`），**未合并**。
2. **`deviation_declared` 规则尚未实现**：决定已记录（§2.5 第 4 条）。SKY130 上 fifo/regfile/mult 的 ss MPW 差为 1.28–1.86 ps。
   - 根因：OpenSTA 驱动端 slew 在电阻上存在 0.35–1.2 ps 的阶跃；RustSTA 连续，更接近 Ngspice。
   - 实现规则并复跑后，这 3 行预计会变为“通过（含声明偏差）”。**此前不能宣称通过。**
3. 因此当前对外口径是 **11/16**。

### 3.2 规模与设计覆盖

- **SERV**（约 4.3k 单元）：
  - GF180 在新 floorplan（利用率 0.35）下超出布线网格节点预算（8M）；SKY130 不收敛。
  - LVS 在 35 个未使用输入端口上失败，这需要负责人裁定规则。
  - Yosys 读 SERV 时 abort，缺少第二个等价 oracle。
- rv_core（约 8k 单元）未跑。尚未声明“工业规模”。
- 顶层金属（met4/Metal4）上的天线违例无法打跳线，仍回落到 diode；SKY130 上 diode 会导致 LVS 失败（deck 限制）。

### 3.3 签核范围之外（明确未声明）

- 无金属/FEOL 填充，GF180 密度失败；SKY130 密度为 unverified。
- 无 IR/EM、SI/串扰、OCV/AOCV、CPPR、多模式；无 pad ring 与 seal ring。
- 提取只有自洽与对外参照，没有经过晶圆厂校准：
  - 在大设计上相对 OpenRCX 差 11–107 ps；
  - 黑盒控制实验表明 OpenRCX 本身不计入单元 pin 金属与过孔焊盘。

### 3.4 其他未完成工作

- **RustLane**：没有集成 RustROAD（W18）；另一位代理的 `feat/postroute-signoff` 仍未提交，需要负责人决定其去留。
- **RustSTA 遗留**：
  - OpenC910 上独立 checker 与引擎相差最多 4.1 ns（先前会话的记录）；
  - SPEF 修订版上的 `whatif insert_buffer` 仍被拒绝；
  - GF180 latch 时序与参考差异较大（夹具中无 latch）；
  - 高电阻（> 1 kΩ）Ceff 残差。
- **推送、合并与 CI**：所有分支未推送，未开 PR，未合入 main，也未在 CI 上运行。
- **磁盘清理**：`RustSys/.work` 约 1.2 TB，`RustSTA/.work` 约 322 GB，`RustROAD/.work` 约 131 GB。还有大量 worktree 待清理。**未删除任何东西。**
- **RustEDA-Studio**：无提交，未纳入本次工作。

---

## 4. 需要独立第三方审查的事项

以下事项要么由同一批 AI 代理“既实现又验证”，存在相关性偏差（违背 AGENTS.md Rule 8 的精神），要么涉及法律或工程判断，必须由独立的人或机构确认。

### 4.1 洁净室与许可（最高优先级）

- **洁净室方法**：RustSTA 的若干行为是黑盒实验推断出来，再“对齐 OpenSTA 3.1.0”的，包括：
  - lumped-pi 的 1e-3 阈值与 1 fF 步长；
  - 一步 Newton Ceff；
  - `--pulse-groups last` 的选择规则；
  - clock-ends 的 MPW 范围。

  代理声明从未阅读参考源码。需要由法务或独立工程师审查实验记录（`RustROAD/.work/sta-signoff/scripts/`、各 HANDOFF），确认整个过程属于黑盒等价推断，不构成对 GPL 代码的派生。
- **GPLv3 规则 deck**（`sky130A_mr.drc`）只被调用，没有复制。需要确认仓库中没有混入该 deck 的任何片段。
- **第三方 RTL**：设计集都来自 RustSys 的自研夹具，SERV 为 ISC 许可。需要确认 RustROAD 中 `fixtures/designs/rtl/` 的来源与许可头。

### 4.2 签核结论的独立复现

- **DRC/LVS**：签核 harness 本身也是代理写的，其中解析器与通过判据可能有盲点；已知 Netgen 会在出错时打印 “match uniquely” 并以 0 退出。
  - 建议第三方用独立脚本或商用工具（Calibre/Pegasus 或 Magic/KLayout 的人工流程）复跑至少 pipeline、regfile、mult 在两个 PDK 上的 GDS。
- **时序**：目前的“正确性”来自两类参照。
  - 一是 RustSTA 与 OpenSTA 的一致性，但 OpenSTA 自身也有已证实的数值伪影（MPW 阶跃、忽略电阻环）。
  - 二是 Ngspice 晶体管级对比。
  - 建议用商用 STA（PrimeTime/Tempus）对同一网表、Liberty、SPEF 做抽样对比。
- **提取**：原生提取只对比过 OpenRCX 与 Magic，没有对照晶圆厂校准的提取器（StarRC/QRC）。在当前数据上不能宣称签核级精度。
- **等价检查**：
  - 主 oracle 是 RustSys 自己的 `check synth`，与 RustSys 有相关性；第二 oracle 是 Yosys。
  - alu 因为 Yosys 读不了 RTL，改用 524,288 组穷举仿真。
  - 建议用独立的形式等价工具复核。

### 4.3 需要人工判断的工程选择（agents 自行做出或建议、已被接受）

- **SKY130 常量驱动**：scale 流程把 `conb_1` 换成了“输入接电源轨的 `inv_1`”（rail-input tie）。**这需要重点审查**：tie 单元本来就是为了避免栅极直接接电源轨（防 ESD 与栅氧应力）；若晶圆厂规则禁止这种做法，这是一个真实的可靠性问题，而当前 DRC/LVS 检查不出来。
- **天线修复**：用跳线代替 diode 是合理的常规做法。但 `diode_2` 在 SKY130 KLayout LVS deck 中不被识别，这一结论需要第三方确认，确认那确实是 deck 的限制，而不是我们的网表问题。
- **`constant_pins` 状态逻辑改动**：该改动用于接受由常量驱动的无到达端点。我审查后接受了，因为它更严格，但改动的是门禁判定逻辑本身，建议再复核一次。
- **GF180 floorplan 利用率从 0.5 调到 0.35**：这是设计选择，不是签核判据，但它影响面积与结果的可比性。
- **负责人决定的合理性**：时钟周期规则、GF180 dont_use、MPW 声明偏差。决定是人做的，但它们在门禁里的实现是否完全符合决定文字，仍需复核。
- **RustSys 基准声明的已知注意事项**（来自先前会话的独立复核）：
  - README 中 Yosys 16 核的时间来自旧一轮测量；
  - Fmax 基线不含缓冲；
  - LEC 黄金模型是 RustSys 自己的检查器。

### 4.4 跨仓库变更

- RustSTA 修改了与 RustSys 共享的文件，包括 Liberty Pin 字段与 `rc.rs`（只做了增量修改）。RustSys 的测试与综合一致性已验证，但仍应由 RustSys 负责人审查（W19）。
- RustROAD 文档与脚本会点名第三方工具；RustSys/RustSTA 的 CONTRIBUTING 禁止这样做。目前做法是差分 harness 只放在 RustROAD 中，这一政策冲突需要正式裁定。

---

## 5. 关键文件与证据位置

- **RustROAD 主文档**：`RustROAD/.work/wt/integrate/docs/`
  - `rtl2gds.md`（最终报告）
  - `closure.md`、`scaling.md`、`signoff.md`、`extraction.md`、`gds-export.md`、`hold-closure.md`
  - 交接文档：`HANDOFF.md` 顶部章节
- **证据 JSON**：`docs/evidence/rtl2gds-final2.json`（最终），以及 `critical-path.json`、`rtl2gds-closure.json`、`rtl2gds-antenna.json` 等。
- **RustSTA**：
  - `HANDOFF.md`（本地、git 排除）
  - `CHANGELOG.md`
  - `docs/specs/sta/`（STA-REQ-0726…0732）
  - 差分与 Ngspice 证据：`RustROAD/.work/sta-signoff/runs/`
- **负责人决定**：`RustROAD/.work/notes/owner-decisions.md`
- **各流 lessons**：`RustROAD/.work/notes/*-lessons.md`
- **侦察与各阶段原始结果**：`RustROAD/.work/understand/{understand,phase-a,phase-b1,phase-b2}.json`

## 6. 建议的下一步（按顺序）

1. 实现 `deviation_declared`，并验证 GF180 DRV 的 WIP 后合并，然后复跑 16 行门禁。目标：16/16，其中注明有声明偏差的行。
2. 先完成 §4.1 与 §4.3 中的第三方审查，再决定是否推送和开 PR。
3. 推送各分支并在 CI 上复跑门禁；清理 worktree 与 `.work`（需逐项确认后再删）。
4. 规模：SERV/rv_core 的布线预算与 LVS 规则；金属填充；RustLane 集成 RustROAD。
