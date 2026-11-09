# CUDA-Rust 在 RustEDA 全系列工具中的加速机会

状态：**分析 + P0/Y1 实测，不是完整合格结果**。写于 2026-11-06。
P0（cut/timing）、Y1（RustSys 综合）、S1（batch DC 与 host companion）和 R1（D0）
已在 RTX 4090 Laptop 上实测。结论：**只有 Y1 可交付**；S1 host 路径与 R1 单谓词实测无收益，
S2 架构上没有 batch 维度，R2 缺规模 fixture 与布局算法前置。所有未测数字明确标为估算。
证据目录：`.work/p0-cuda/evidence/`。

## 0. 一页结论

1. **cuda-rust 可用，而且项目里已经有两个真实的 GPU 先例**：RustSpice 的 cuda-oxide PTX 批量 DC，
   以及 RustEDA-Common 的 CUDA C cut/timing 后端（NVRTC 路线，不是 cuda-rust）。
   **P0 实测修正**：cut 在 10 万随机节点上 2.49×；timing 在 100 万–200 万节点上只有 0.28–0.31×
   （GPU 更慢）。**T1 应放弃**，不要在生产中启用 timing 的 CUDA 后端。
2. **但在 Ibex 这条 flow 上，GPU 已经不是大杠杆了**。C7 之后，Rust 代码自己只占约 371 s / 869 s
   （42.7%），另外 57% 是 KLayout 签核。即使把所有 Rust CPU 工作变成无限快，当前串行布局的理论上限也只有
   **约 1.75×**；GPU 现实的端到端收益约 **60–100 s（7–11%）**。
3. **实测后，只有 Y1 cut 站得住**：
   - **Y1**：cut 阶段 2.0×、e2e 1.05×、netlist 相同；
   - **S1**：batch DC 2.39× @ 5×10⁵ corners；host companion 瞬态 0.63–0.78× → **否决**；
   - **R1**：D0 pair-filter 0.83–1.19× → 单谓词无收益，完整 G1 未证；
   - **S2**：伴随已是单次转置解 + O(1) 收缩，**没有 batch 维度，关闭**；
   - **R2**：无 10⁵+ placement fixture，前置是布局算法升级，**推迟**。
4. **建议（P0/D0/S1 实测后）**：只把 Y1 作为可交付的 GPU 加速；S1/S2/R1/R2 在拿到规模证据前不立项。
   不要为 Ibex 的 A* 布线或 SAT/CEC 上 GPU。
5. **QoR 安全的分层**：整数几何/集合运算（A 类，产物哈希逐字节相同）> 定点原子规约（B 类，跨后端逐位一致）
   > f64 重排（C 类，只能给容差 + 端到端 QoR 门）。优先做 A/B，C 类只在 RustSpice 这种本来就有容差门的场景做。
6. **集成形态已经定型**：同一份 Rust 源码编译两次（CPU `include!` + cuda-oxide PTX），PTX 提交进仓库，
   运行期只需要 `libcuda`。这条路径不需要系统 CUDA、不需要 NVRTC，最适合 flow 的 podman 容器。

### 0.1 P0 / Y1 / S1 / R1 实测（2026-11-06，RTX 4090 Laptop）

环境：i9-13900HX 32 线程，RTX 4090 Laptop（sm_89），driver 615.78.08 / CUDA 13.4，
NVRTC 13.5（PyPI wheel）。所有时间都是端到端，含传输与启动；中位数。

**P0：cut 可用，timing 不可用**

| 任务 | 规模 | CPU 32T | CUDA | 加速比 |
| --- | ---: | ---: | ---: | ---: |
| cut | 10 万节点 | 1060 ms | 426 ms | **2.49×** |
| cut | 20 万节点 | 2127 ms | 1169 ms | 1.82× |
| timing | 100 万节点 | 137 ms | 486 ms | **0.28×** |
| timing | 200 万节点 | 297 ms | 966 ms | 0.31× |

7 个 GPU 差分测试全部通过：cut 结果逐字相同，timing f64 逐位相同。结论：
**Y1 的 cut 后端可以进入生产评估（大设计的 cut 阶段 ~2×；需在 flow 构建里显式开 feature 并补 receipt）；
T1 的 timing 后端应明确不启用。**

**Y1：真实综合上的 cut 加速**

RustSys 自带 `benches/backends.rs`：同一 netlist 上比较 CPU / CPU-task / CUDA，并断言三者 netlist 相同。

| lanes | CPU 32T 总 | CUDA 总 | CPU cut | CUDA cut | cut 加速 | netlist |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 64 | 2.161 s | 2.060 s | 0.624 s | 0.313 s | 2.00× | 相同 |
| 128 | 4.014 s | 4.030 s | 1.161 s | 0.691 s | 1.68× | 相同 |
| 256 | 7.687 s | 7.269 s | 2.147 s | 1.167 s | 1.84× | 相同 |

端到端只有 1.00–1.06×，因为 exact-area/planning 阶段仍是 CPU 且占比越来越大。
QoR 由构造保证：基准在每次运行后 `assert!(netlist == previous)`。

**S1 基线：batch DC 的真实上限**

| corners | CPU batch API | cpu_ref | CUDA | CUDA vs 最快 CPU |
| ---: | ---: | ---: | ---: | ---: |
| 1,024 | 3.2 ms | 3.6 ms | 10.5 ms | 0.30× |
| 16,384 | 28.4 ms | 23.9 ms | 17.0 ms | 1.40× |
| 131,072 | 217 ms | 177 ms | 103 ms | 1.71× |
| 524,288 | — | 680 ms | 285 ms | **2.39×** |

这推翻了此前"7.0×–35.0×"的记录在本机上的可复现性；batch kernel 是 FP64/带宽受限，
而 32 线程 CPU 很快。**S1 的合理预期应改为 ~2×，且只在大 batch 时成立。**

**R1 / D0 重跑：bit-exact 通过，但不是加速**

用当前工作树重建 CPU gate（4/4）和 PTX manifest（14 个源码哈希 + PTX 7,463 B），
真机差分 2/2 通过，整数容差为零。`examples/compare`（32,768 shapes / 2,048 queries，
含 packing 与传输）：

| pairs | CPU call | GPU call | CPU/GPU |
| ---: | ---: | ---: | ---: |
| 1,024 | 0.08 ms | 0.19 ms | 0.41× |
| 65,536 | 0.47 ms | 0.50 ms | 0.94× |
| 1,048,576 | 3.90 ms | 4.21 ms | 0.93× |
| 8,000,000 | 20.68 ms | 24.89 ms | 0.83× |

最好也只有 1.19×（1M pairs）。**pair-filter 本身不是可用的加速点**；CPU 已经受带宽限制且很快。
R1 若要做，必须把区间裁剪、排序、重叠/间距一起搬上 GPU，并用 SPEF 哈希做门；
在拿到这个证据之前，不应再把 R1 写成 3–6×。

**S1 host-side companion：机制通，但不是加速**

用 `ParamSlot::SourceValue` 把电容换成 backward-Euler companion（C/h 电阻 + 历史电流源），
复用现有 batch kernel；CPU ref 与 GPU **逐位一致（max abs diff 0e0）**。但每步状态向量的
H2D/D2H 主导：

| corners | steps | CPU ref | GPU | CPU/GPU |
| ---: | ---: | ---: | ---: | ---: |
| 16,384 | 50 | 17.76 ms | 28.02 ms | 0.63× |
| 131,072 | 20 | 42.30 ms | 56.06 ms | 0.75× |
| 524,288 | 10 | 72.86 ms | 93.31 ms | 0.78× |

即使 52 万 corners × 10 步也只有 0.78×。**host 路径否决**；要做必须在 device 上保持时间循环
（persistent kernel），而 DC batch 的天花板只有 2.39×，不值得。

**S2 伴随：没有 batch 维度，机会关闭**

`AdjointSensitivityEngine::compute_dc_adjoint_gradients` 已经是一次
`J^T λ = -dL/dx` 转置求解，然后对每个参数做 `λ^T ∂F/∂θ` 的 **O(1) 收缩**；
不存在"每个参数解一次"的 RHS 批量。唯一可 GPU 化的是那一次稀疏转置求解，
规模小、延迟主导，不构成"大幅加速"机会。**S2 关闭，不是"未实现"。**

**R2 布局：缺规模与算法前置**

`PlaceLimits::max_cells = 250_000`，但仓库里没有 10⁵+ cell 的 placement fixture；
Ibex 24k cell 的布局只要 1.6 s。R2 的前置是 P1/ePlace 的密度/线长算法升级，
不是 GPU 本身。**R2 推迟，等待规模 fixture 与算法升级。**

---

## 1. NVIDIA/cuda-rust 是什么，哪个模型适合 EDA

来源：<https://github.com/NVIDIA/cuda-rust>（Apache-2.0；README 自述 early-stage research project，
"Expect bugs, incomplete features, and API breakage"）。

| 组件 | 模型 | 编译方式 | 工具链 | 对 EDA 的适配 |
| --- | --- | --- | --- | --- |
| **cuda-oxide** | SIMT（线程/block/shared memory） | AOT，Rust → PTX，`cargo oxide` | 固定 Rust **1.99.1** + `rustc-dev`、LLVM 21+、CUDA 13+ | **适合**：稀疏、指针追逐、不规则控制流、整数几何 |
| **cutile-rs** | Tile（张量 tile，NumPy 风格） | 首次 launch 时 JIT，Rust → Tile IR → cubin | Rust ≥ 1.89；sm_89 需要 CUDA 13.2+ 的 `tileiras` | **不适合**：EDA 的数据是稀疏图/区间/几何，不是稠密 tile |
| cuda-bindings / cuda-core / cuda-async | host runtime | — | 需要 `cuda.h`/bindgen | 可不用；项目已有 cudarc 0.19.10 |

### 1.1 结论

- **只用 cuda-oxide，不用 cutile-rs。**
- **host 端不引入 `cuda-core`**：它需要 bindgen 和 `cuda.h`，而 `rusteda-compute` 已经有 cudarc
  0.19.10，能直接 `Ptx::from_src` 加载预编译 PTX（D0 实验已验证），RustSpice 则用 `libloading`。
- **锁定 revision**：仓库里已固定 `a4a939a9b5f01f3bf712e92386922e1bc7c434a9`，PTX 与源码哈希一起提交。
  升级 cuda-rust 是一次显式的、带重新生成 PTX 的动作，不是 `cargo update`。
- **工具链隔离**：cuda-oxide 的 `gpu/` 子 crate 自带 `rust-toolchain.toml`（1.99.1），与主仓库 1.97.1
  并存；主构建完全不依赖它。
- **许可证/clean-room 无冲突**：cuda-rust 是编译器/运行时，不是参考 EDA 工具；Apache-2.0 与本项目一致。

### 1.2 治理缺口（需要明确决定）

`AGENTS.md` Rule 5.2 目前写的是 "Linux GPU kernels use native CUDA (`cudarc`, `.cu`, `cuFFT`, `cuSPARSE`)"。
cuda-oxide 产出的是原生 PTX，走同一条 driver API，但它不是 `.cu`/cudarc。2026-10-31 的交接记录里用户已口头
批准 cuda-oxide PTX 用于 Common/RustROAD；但规则文本没改。

建议修订为："Linux GPU kernels use native CUDA via cudarc/NVRTC CUDA C **or precompiled PTX produced by a
pinned cuda-rust (cuda-oxide) revision and loaded through the CUDA driver API**; no cross-platform GPU kernel
language." 这样保留了 Rule 5 的原意（原生 CUDA、每核先有 `cpu_ref`、差分测试），又承认已有先例。

---

## 2. 现状盘点：哪些已经是 GPU，哪些只是"有后端"

| 位置 | 任务 | 后端 | 状态 | 是否进入合格 flow |
| --- | --- | --- | --- | --- |
| `rusteda-compute/src/timing` | STA arrival/slew 传播 | CPU + CUDA C (`timing.cu`, NVRTC) | f64 与 CPU 逐位一致；f32 有显式容差 | **否**：`cuda` feature 默认关闭 |
| `rusteda-compute/src/cut` | 工艺映射的 priority-cut pass | CPU + CUDA C (NVRTC) | 有 `cuda_equals_cpu_on_random_problems`（`#[ignore]`，需 GPU） | **否**：同上 |
| `rusteda-compute/src/geometry` | 矩形 pair 谓词（D0） | CPU + cuda-oxide PTX | RTX 4090 上整数 zero-tolerance 通过 | 实验，未接生产 |
| `RustSpice/backend-cuda-simt` | 批量 DC Newton | cpu_ref + cuda-oxide PTX | 实测 7–35×；差分 1e-12 | 是（可执行，非 RTL2GDS） |
| `RustSpice/backend-cuda-tile` | 所谓 Tile 后端 | **CPU delegate** | 只探测 driver、生成 kernel 字符串，实际全部回退 CPU | 否；**不是 cutile-rs 先例** |
| `RustROAD` | 提取/布局/布线 | **纯 CPU** | 不依赖 `rusteda-compute` | — |
| flow | 编排 | 纯 Python/Rust | podman 容器，无 GPU 透传参数 | — |

**两个关键事实：**

1. `RustSys`、`RustSTA` 的 `cuda` feature 都是 opt-in，默认构建是 CPU-only；合格基准的二进制没有证据表明开了 GPU。
2. RustROAD 是唯一完全没接 `rusteda-compute` 的引擎，而它恰好是当前 flow 里最大的自有 CPU 消耗。

---

## 3. 时间到底去哪了（C7 replay 的 step receipts）

证据：`.work/perf-20261030/c7-producer-fix01/replay02/output/step-receipts.json`（106 steps，合计 868.9 s；
已接受的 C7-only 整跑是 887.289 s）。

| Step | 内容 | Wall (s) | 占比 | CPU 归属 |
| --- | --- | ---: | ---: | --- |
| `be_ff_main` | KLayout foundry main DRC | 314.9 | 36.2% | **外部** |
| `be_route` | RustROAD 布线 | 196.8 | 22.7% | RustROAD |
| `be_ff_antenna` | KLayout foundry antenna | 178.7 | 20.6% | **外部** |
| `be_postroute` | 提取 + RustSTA（`native_postroute.py`） | 106.1 | 12.2% | RustROAD/RustSTA |
| `fe_*`（约 95 个） | 综合/映射/ECO/place+CTS/STA | 68.5 | 7.9% | RustSys/RustSTA + 进程启动 |
| `be_antenna_repair` | 原生天线修复（C7 后） | 1.1 | 0.1% | RustROAD |
| 其余 | 导出/审计 | ~2.8 | 0.3% | — |

### 3.1 由这张表得到的硬约束

- **外部签核 493.5 s（56.8%）**。这部分不是我们的代码，GPU 碰不到。
- **自有 CPU 371.4 s（42.7%）**：route 196.8 + postroute 106.1 + frontend 68.5。
- 当前串行布局下，**任何 Rust 侧优化的理论上限**是
  `868.9 / (868.9 − 371.4) = 1.75×`。
- 对比 run16（2,936.6 s）：C7 已经把天线修复从 858 s 降到 1.1 s，把 postroute 从 560.6 s 降到 106.1 s。
  **提取不再是 1,081 s 的大头**，所以 2026-10-30 交接里 "GPU 省 100–150 s" 的估计现在要下调到 **60–100 s**。

### 3.2 GPU 现实收益估算（Amdahl）

| 可 GPU 化的自有工作 | 估计占比 | 乐观 kernel 加速 | 端到端节省 |
| --- | ---: | ---: | ---: |
| 提取几何（postroute 的 ~70%） | ~74 s | 5× | ~59 s |
| 布线里的代价评估/拥塞矩阵（route 的 ~20%） | ~39 s | 5× | ~31 s |
| STA 传播（postroute 的 ~10%） | ~11 s | 4× | ~8 s |
| **合计** | ~124 s | — | **~98 s ≈ 11%** |

这不是"大幅加速"。**在 Ibex 上继续追 GPU 的边际收益很低**；要大幅提速必须动外部签核的并行度或布线算法。

### 3.3 如果排除 KLayout

"排除 KLayout"有两种含义，结论不同：

1. **只把 KLayout 从时间账里去掉，仍要求它签核**：分母变成自有工具的 371.4 s。
   GPU 现实节省仍约 98 s（提取几何 ~59 s + 布线代价 ~31 s + STA ~8 s），
   371.4 → ~273 s，**自有工具口径 1.36×**；GPU 占自有时间约 26%，比含签核时的 11% 高一倍多，
   但绝对收益没有变。
2. **用自研 DRC/LVS 替代 KLayout**：这不是"加速"，是换签核引擎，必须逐条 foundry deck 等价、
   双 PDK 回归、并获得签核认可；风险远大于收益，且与 cuda-rust 无关。不建议放进这一轮。
3. **完全不做签核**：16 门不完整，不能发布，也不能算 QoR 不变。

所以：排除 KLayout 后，GPU 的相对价值上升，但**首选仍是 R1 的完整几何段**（A 类、逐字节相同）；
注意 D0 已证明只有 pair-filter 不够，必须把裁剪/排序/重叠一起搬上 GPU 才有机会。
不要因为分母变小就把 S1 的 2× 当成 5×。

---

## 4. GPU 适配性判据（为什么 EDA 大多不适合）

一个阶段值得上 GPU，需要同时满足：

1. **规则/稠密**：同样大小的独立工作项 ≥ 10⁴，且访存可合并。
2. **算术强度足够**：每字节 ≥ 10 flop/整数操作，否则 PCIe 成为瓶颈。
3. **bulk-synchronous**：一个 launch 内没有跨工作项的动态依赖；rip-up/backtrack 不适合。
4. **确定性可实现**：整数/定点规约可用交换律原子；f64 规约必须固定顺序。
5. **数据常驻**：能在多次迭代间留在显存；否则每次 H2D/D2H 会吃掉收益。
6. **QoR 可证明**：要么产物哈希不变，要么有独立的容差门和端到端 QoR 门。

按这个判据：

| 适合 | 不适合 |
| --- | --- |
| 区间/矩形集合运算、DRC 式几何谓词 | detailed routing 的 A*、rip-up & reroute |
| 逐 net/逐 corner 的独立数值批处理 | SAT/CEC、增量时序的拓扑遍历 |
| 密度散射、HPWL 分段规约、SpMV | 解析、elaboration、文件 I/O |
| 固定点数的 LUT 插值、查表 | 动态内存、递归、指针追逐的图遍历 |

补充两个硬件事实：

- **GeForce 的 FP64 只有 FP32 的 1/64**（RTX 4090 Laptop）。EDA 核要把重活放在 **整数/定点** 上；
  f64 只适合小体量、高价值的 STA 类计算。
- **i64 在 sm_89 上比 i32 慢**。DBU 坐标在 1 nm 精度下，100 mm 芯片也只有 10⁸ DBU，**能放进 i32**；
  kernel 应做 i32 快路径 + i64 溢出回退，而不是全程 i64。

---

## 5. 机会矩阵

QoR 类别：**A** = 产物逐字节相同；**B** = 定点规约，跨后端逐位一致；**C** = 容差 + 端到端 QoR 门。

| ID | 工具/阶段 | 核 | 现状 | kernel 加速（估） | Ibex 端到端 | QoR | 规模价值 | 工作量 |
| --- | --- | --- | --- | ---: | ---: | --- | --- | --- |
| **S1** | RustSpice 批量瞬态 | 每时间步 × 每 corner 的 Newton 批处理 | **host companion 实测 0.63–0.78×** | **否决（host 路径）** | 无 | C | 低 | — |
| **S2** | RustSpice 批量伴随/AC | 同一 LU、多 RHS / 多频点 | **已分析：伴随是单次转置解 + O(1) 收缩** | **无 batch 维度，关闭** | 无 | C | 无 | — |
| **R1** | RustROAD 提取几何（G1） | 区间裁剪、重叠长度、间距，i64 DBU | **D0 实测 0.83–1.19×**；完整 G1 只有 compiler feasibility | 未证（pair-filter 无收益） | 未知，不能按 40–70 s 计 | **A** | 待重新论证 | L |
| **R2** | RustROAD 布局密度/线长（G2） | 密度散射 + Poisson/DCT + HPWL 梯度 | 无 10⁵+ fixture；前置布局升级 | 未证 | ~0（24k cell 1.6 s） | B/C | 待定 | L |
| **R3** | RustROAD 全局布线代价 | 每 net 候选代价批量评估 | 未开始 | 2–5×（仅代价段） | ~10–30 s | B | 中 | M |
| **Y1** | RustSys cut-pass 映射 | 已有 CUDA 后端 | **已验收**：2.0× cut，1.05× e2e | 2.0× | ~0（Ibex 1.2 s） | **A**（netlist 相同） | 高 @ 大规模 | S |
| **T1** | RustSTA 传播 | 已有 CUDA 后端 | **已测：0.21–0.31×，放弃** | <1× | 负收益 | A（f64 逐位） | 无 | — |
| **C1** | Common 矩形谓词（D0） | 已通过 | **实测 0.83–1.19×** | 0.8–1.2× | 属于 R1 | A | 低 | S |
| — | detailed route A* | — | — | <1.2× | — | — | — | 不做 |
| — | SAT/CEC | — | — | 研究级 | — | — | — | 不做 |
| — | 解析/flow/签核替换 | — | — | — | — | — | — | 不做 |

### 5.1 不该做的事

- **不要给 detailed routing 的 A* 上 GPU**：每个 expansion 依赖 Spatial 查询和已布金属，分支密集，
  warp divergence 会把收益吃光。这里的正确杠杆是 CPU 并行（pin access 候选按 terminal 并行）+ 代价模型。
- **不要用 GPU 替换 KLayout 签核**：foundry gate 必须用原 deck；自写 DRC 只能做"预筛"，不能当 gate。
- **不要为 Ibex 的 SAT/CEC 上 GPU**：`nf_*` 只有 12.9 s，而且 SAT 是分支密集型。
- **不要相信 "Tile 后端已存在"**：`rustspice-backend-cuda-tile` 目前是 CPU delegate，没有链接 cutile-rs。

---

## 6. 三个值得做的方案

### S1. RustSpice：把 batch DC 扩成 batch transient / AC / adjoint（host 路径已实测否决）

**现状**

- `rustspice-solver/src/batch_dc.rs` 已经用 `CudaBatchSolver` 做"每 corner 一个线程"的 DC Newton；
  kernel 源码 `batch_newton.rs` 同时被 CPU `include!` 和 cuda-oxide 编译。
- 实测（RTX 4090 Laptop，本机 2026-11-06）：batch DC 在 524,288 diode corners 上比最快的
  32 线程 rayon CPU 快 **2.39×**；131,072 corners 上 1.71×；16,384 corners 上 1.40×；
  ≤1,024 corners 时 CPU 更快。此前记录的 7.0×–35.0× **在本机不可复现**，不应再作为规划依据。
- `transient.rs`、`adjoint.rs` 仍是纯 CPU；`Simulator::run_batch_dc_op` 是唯一的 batch 入口。

**做法**

1. **Batch transient**：时间步循环留在 host，每个时间步对 M 个 corner 并发一次 batch Newton；
   每个 corner 独立收敛判定，失败者回退 CPU solver。
   - **机制已打通，但性能否决**：`ParamSlot` 已经有 `SourceValue` 和 `Conductance`，kernel 的
     `RHS_DSRC` / `JAC_DSRC` 会按 corner 读取它们；用 backward-Euler companion model
     （电容 → C/h 电阻 + 历史电流源）在 host 侧构造固定 topology，CPU ref 与 GPU 逐位一致。
     但每步要传回整个状态向量，实测 0.63–0.78×（见下）。
   - 复用现有 `BatchPlan`/static-pivot LU；每步只更新 params 并调用一次 `solve`。
   - 前置：`BatchPlan::build` 目前要求至少一个非线性器件；纯线性 RC 需要放宽这个检查或加线性路径。
   - 关键：**不改变 Newton 容差、gmin/source stepping 顺序、收敛判据**；GPU 只做"同一套迭代的并行副本"。
2. **Batch adjoint**：伴随法对每个参数解一次 `Aᵀx = ∂f/∂p`，A 的分解可复用；把 RHS 按 batch 维度
   送 GPU（SpSV 或 level-scheduled triangular solve）。参数上千时这是天然的大 batch。
3. **Batch AC**：多频点独立复解；先做实数块 2×2 形式，避免 complex kernel。

**QoR 门**

- GPU vs `cpu_ref`：相对误差 ≤ 1e-12（沿用 batch_dc 的门）。
- `cpu_ref` vs CPU solver：≤ 1e-9（Newton 容差）。
- 每个收敛 corner 的 KCL 独立检查（现有测试已有）。
- 与 ngspice-47 黑盒对比：不放松任何 `reltol/abstol/vntol`；收敛 corner 数必须一致。
- 端到端计时包含 H2D/D2H、JIT、同步；报告 cold/warm 两组。

**实测（2026-11-06）**：host-side companion 复用 batch kernel，CPU ref 与 GPU 逐位一致，
但 16k/131k/524k corners 分别只有 0.63×/0.75×/0.78×；每步 H2D/D2H 状态向量主导。
**结论：host 路径否决。** 若还要做，必须把时间循环和状态留在 device（persistent kernel），
这是更大的改动，而 DC batch 的天花板只有 2.39×，不建议继续投入。
**风险**：即使 device-resident，收益上限仍受 FP64/带宽限制；不要按 5–20× 立项。

### R1. RustROAD 提取几何：bit-exact 的最大单点

**现状证据**（run16 extract 日志）

| 指标 | 值 |
| --- | ---: |
| nets | 16,329 |
| wire_runs | 520,692 |
| shapes | 2,735,374 |
| intervals | 17,680,976 |
| passive_intervals | 14,937,021 |
| work | 5.596 × 10¹⁰（预算 1e11 的 56%） |
| max_run_candidates | 15,881 |
| SPEF | ~61 MB |

当前 `extract_cpu_ref` 是**串行**的（`extract.rs` 里 `par_iter` 计数为 0），每个 run 对全部 RC 层做宽窗口
bin 查询。C7 之后 postroute 只剩 106 s，但大规模设计上 work 会线性/超线性增长，这是最先触顶的地方。

**做法（沿用 2026-10-30 交接的 B1 拆分）**

1. 保持 `extract_cpu_ref` 的"几何 → 数值"两阶段：几何段（候选收集、`subtract`、`uncovered`、
   `face_portions`、重叠长度、间距）全部 i64 DBU，搬到 GPU。
2. 输出按 (run, side) 排序的区间表（约 1,770 万条 × 24 B ≈ 425 MB，PCIe 单程约 20–35 ms）。
3. **f64 采样与累加仍留在 CPU，按原 run_index 顺序**；SPEF 逐字节不变。
4. 用 D0 的 `VerifiedPtx` 模式：源码哈希 + PTX 哈希 + 参数 ABI 校验，在创建 CUDA context 之前完成。

**QoR 门**：同一 workspace replay，SPEF sha256 与 CPU 版**逐字节相同**（不是容差）。
这是 A 类，最强保证。

**预期（D0 修正后）**：单 pair-filter 实测 0.83–1.19×，**没有收益**；只有把完整几何段
（候选收集 + 区间裁剪 + 排序 + 重叠/间距）都搬上 GPU，并用 SPEF 逐字节哈希证明，才可能达到 3–6×。
在拿到这个证据之前，Ibex 的 40–70 s 收益不能计入规划。

### R2. 大规模布局：密度/线长核（只在 ≥10⁵ cells 时做）

**现状**：`place.rs` 是"二次线长 CG + 递归二分扩散 + 贪心合法化 + 详细布局"，Ibex 24k cell 约 1.6 s。
P1 诊断还表明：**200 轮布局的 HPWL 反而比 24 轮差 4.06%**，瓶颈在合法化/目标实现，不在迭代次数。
所以现在上 GPU 没有意义。

**做法（前置：P1/ePlace 升级合入并通过 QoR 门）**

- 密度散射用 **i64 定点原子**（Rule 6.3），Poisson 用 cuFFT（经 cudarc）或自写 DCT。
- HPWL 梯度按 net 分段规约；用固定分块 + 树形归约保证确定性。
- 与 `cpu_ref` 的确定性模式逐位一致；做不到时，用 3 个种子的 HPWL 偏差 ≤ ±0.5% + 全 flow QoR 不劣作为门。

**预期**：10⁵ cells 3–5×，10⁶ cells 5–10×；Ibex 上 0。

### Y1/T1. 已建未用：先把现有后端跑起来

这是**最低成本、最高确定性**的一步：

- 用 `--features cuda` 构建 RustSys/RustSTA，在 RTX 4090 上跑 `#[ignore]` 的
  `cuda_equals_cpu_on_random_problems` 和 timing 差分测试。
- 在 10 万门级 fixture 上测 cut pass 的加速比；在 8 corner 上测 timing。
- 如果 < 1.5× 或只在 < 1 s 的任务上有收益，就明确记录"不值得进 flow"，避免后续重复投入。

---

## 7. 集成架构（照 RustSpice 的成功形态）

```text
rusteda-compute/src/<task>/
  kernels/<task>.rs        # 同一份源码：CPU include! + cuda-oxide 设备代码
  cpu.rs                   # 权威 cpu_ref（rayon）
  cuda.rs                  # host glue：cudarc 加载预编译 PTX
  gpu/                     # 独立 crate，rust-toolchain 1.99.1，不属主 workspace
    Cargo.toml             # cuda-device/cuda-host/cuda-core @ pinned rev
    build_ptx.sh           # cargo oxide build --no-fmad --arch sm_80
  <task>.ptx               # 提交进仓库
  <task>.ptx.sha256
```

要点：

1. **同一份源码两编译**，杜绝 CPU/GPU 语义漂移。
2. **PTX 提交进仓库**：主构建不需要 CUDA、不需要 cuda-oxide、不需要 1.99.1；运行只需要 `libcuda`。
3. **`--no-fmad`**：只有显式 `mul_add` 融合，保证与 CPU 舍入一致。
4. **host 用 cudarc 的 `Ptx::from_src`**（D0 已验证）或 `libloading`；不引入 `cuda-core` 的 bindgen 依赖。
5. **feature 默认关闭**；`cuda` 打开时才编译 GPU glue。无 GPU / 自检失败 → 回退 `cpu_ref`，receipt 写
   `backend: cpu_ref`，绝不静默冒充 GPU。
6. **容器**：flow 走 podman，需要在容器里加 `--device nvidia.com/gpu=all`（CDI）并挂载 `libcuda`。
   预编译 PTX 路线不需要在镜像里装 CUDA toolkit/NVRTC，比 cudarc+NVRTC 更适合现有镜像。
   **Y1 的生产前提**：现有 cut 后端走 NVRTC，而 flow 镜像里没有 NVRTC；启用前必须二选一：
   镜像补 NVRTC，或把 cut kernel 也用 cuda-oxide 编成 PTX（后者更符合现有容器形态）。
   T1 已否决，不需要做这个迁移。
7. **内存**：cgroup `MemoryMax` 不管显存；用 `systemd-run --user --scope -p MemoryMax=…`，
   **不要**用 `ulimit -v`（会破坏 CUDA 虚拟地址保留）。显存预算单列，不超过空闲显存的一半。

---

## 8. 验收门（按 Rule 8，不许只报 kernel 时间）

每个 GPU 核必须同时满足：

1. **独立 oracle**：`cpu_ref` 与 GPU 不共享"唯一验证假设"；整数核用独立重算/哈希，f64 核用 KCL/ngspice/OpenSTA。
2. **差分测试**：A 类零容差；B 类逐位；C 类显式容差 + 端到端 QoR。
3. **产物门**：R1 用 SPEF sha256；R2 用 HPWL/面积/时序表；Y1/T1 用 netlist/timing 哈希。
4. **端到端 wall**：包含 lowering、H2D/D2H、JIT/PTX 加载、同步；cold/warm 分开。
5. **资源**：`/usr/bin/time -v` peak RSS + 显存峰值；普通测试 < 2 GiB，PDK 验证 < 8 GiB。
6. **双 PDK**：sky130 + gf180mcu（STA/映射/提取都适用）；IHP 另记。
7. **16 门不变**：任何 GPU 路径都不能放松签核门。
8. **基准公平性**：主比较仍是 CPU-only；GPU 结果单列一行，receipt 记录 GPU 型号、driver、cuda-rust rev、
   PTX sha256。不能声称 LibreLane "不能用 GPU"（它的构建带 +GPU feature，只是默认没开）。

---

## 9. 路线图

| 阶段 | 内容 | 时间（估） | 退出条件 |
| --- | --- | --- | --- |
| **P0** | 打开 `cuda` feature，在 4090 上跑 cut/timing 差分与基准 | — | **已完成（2026-11-06）**：cut 2.49×@10 万节点；timing 0.28–0.31×，放弃 T1；Y1 综合 2.0× cut / 1.05× e2e，netlist 相同 |
| **P1** | RustSpice batch transient（S1） | — | **实测否决**：host companion 0.63–0.78×；只剩 device-resident 路线，天花板 2.39×，不建议继续 |
| **P2** | 提取几何 G1（R1） | 3 周 | SPEF 逐字节相同；Ibex 省 ≥40 s；OpenC910 规模 ≥3× |
| **P3** | RustSpice adjoint/AC（S2） | 2 周 | 千参数伴随 ≥3×；梯度与 FD 门一致 |
| **P4** | 大规模布局 G2（R2） | 3–4 周 | 10⁵+ cells ≥3×；QoR 不劣 |
| 并行 | Rule 5 措辞修订、基准 receipt 字段、CPU+GPU 发布行 | — | 评审通过 |

**不建议**把 Ibex 的 GPU 化放进关键路径：它的上限只有 ~11%；实测后也没有证据表明 S1/R1 能在规模上补回来。

---

## 10. 风险与未决问题

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| cuda-oxide 是 alpha，API/工具链会变 | 构建断裂 | 固定 rev、PTX 入库、升级走显式重建 |
| 主工具链 1.97.1 vs cuda-oxide 1.99.1 | CI 复杂 | 只在 `gpu/` 子 crate 用 1.99.1；主构建不依赖 |
| 本机当前无 `/dev/nvidia*` | 无法实测 | 在 4090 机器上跑 P0；本轮结论标注未验证 |
| GeForce FP64 1/64 | f64 核收益低 | 优先整数/定点；f64 只做小体量 STA |
| 显存不受 cgroup 限制 | 宿主 OOM | 显存预算 + `MemoryMax` + 单飞 |
| 容器无 GPU 透传 | flow 里用不了 | CDI `--device nvidia.com/gpu=all`；预编译 PTX 免 toolkit |
| 基准不公平 | 发布争议 | CPU-only 主行 + CPU+GPU 副行 + 完整 receipt |
| Rule 5 文本未改 | 评审阻塞 | P0 同步提规则修订 |

---

## 附录 A：证据位置

- 当前时间分布：`.work/perf-20261030/c7-producer-fix01/replay02/output/step-receipts.json`
- C7/T1 结果：`JOINT_T1_VALIDATED31.json`、`docs/handoffs/2026-10-31-rusteda-perf-qor-claude.md`
- run16 提取统计：`docs/handoffs/2026-10-30-rusteda-perf-qor-codex.md` §3.2
- GPU 先例：`modules/RustSpice/crates/rustspice-backend-cuda-simt/kernels/README.md`
- D0 实验：`modules/RustEDA-Common-g1-prototype/experiments/rc_geometry_d0/README.md`
- cut/timing 后端：`modules/RustEDA-Common/crates/core/rusteda-compute/src/{cut,timing}`
- 提取代码：`modules/RustROAD/crates/rustroad-core/src/extract.rs`、`extract/geometry.rs`
- 布局代码：`modules/RustROAD/crates/rustroad-core/src/place.rs`
- 布线代码：`modules/RustROAD/crates/rustroad-core/src/troute.rs`

## 附录 B：复现命令（GPU 机器）

```sh
# 已有后端验收（RustEDA-Common）
cd modules/RustEDA-Common
cargo test -p rusteda-compute --features cuda --release -- --ignored --nocapture

# RustSpice 批量 DC 差分
cd modules/RustSpice
RUSTSPICE_REQUIRE_GPU=1 cargo test --release -p rustspice-solver --test batch_dc

# 重建 PTX（只在改 kernel 源码后）
CARGO_OXIDE=$PWD/../../.work/gpu/cuda-rust/cuda-oxide/target/debug/cargo-oxide \
CUDA_TOOLKIT_PATH=$PWD/../../.work/gpu/cuda-13.4 \
./crates/rustspice-backend-cuda-simt/kernels/build_ptx.sh sm_80
```
