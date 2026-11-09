# RustEDA 性能与 QoR：Codex → Claude 交接（2026-10-31）

工作目录：`/home/cgao/git/RustEDA`。本文接续 `docs/handoffs/2026-10-30-rusteda-perf-qor-codex.md`，不替代根 `AGENTS.md`、`HANDOFF.md` 和模块说明。路径中的 `W` 表示 `.work/perf-20261030/`。本轮原生执行、独立审计和收尾均已结束；保留的失败与未完成事项如下。

## 用户授权与工作边界

- 用户已经批准 cuda-oxide 生成 PTX，经现有原生 CUDA runtime 使用，并保留 `cpu_ref` 回退和显式容差差分测试；不要再次询问这个例外。
- 用户已经批准隔离的 staggered-tap T1 实验；必须独立检查 LU.a/LU.b，并保持原有 16 个签核门。
- 用户已经要求清理 `.work` 下过时无用的实验和构建产物后继续。已完成一次有清单的清理，回收 **19,182,342,144 B，约 17.865 GiB**。仅删除 32 个旧 Cargo 对象目录；源码、ELF、工作树、历史证据、归档和 `.work/tools/sha256` 均保留。清单在 `.work/cleanup-20261031/`，不要扩大为无清单的 `rm -rf`。
- 用户本次最新指令：**完成当前轮后写交接文档，剩余工作由 Claude 完成**。因此本轮不再启动新的主 G1 编译、GPU 执行或完整联合 flow。
- 没有 push、PR、root gitlink 更新、reset、clean 或 stash 授权。各模块保持独立历史；不要建立根 Cargo workspace。保留所有脏模块和实验工作树。
- 参考 EDA 工具只作黑盒；不读、diff 或移植其源码。PDK/deck/AppImage 在相关检查中仅作不解释内容的哈希、复制和黑盒运行。

## 已完成的端到端结果

| 结果 | 实测端到端时间 | 验证范围 |
|---|---:|---|
| 原始 run16 | 2936.6 s | 原有 16/16 门，通过的只读基线 |
| C7-only | **887.289403718 s** | 已接受的独立全流程结果 |
| C7 + T1 | **1394.718814854 s** | 128/128 steps、16/16 gates；准备 15.481432950 s + 启动/执行 1379.237381904 s |

C7 + T1 的根验收是 `W/JOINT_T1_VALIDATED31.json`，SHA `6da5610bf4ae2a2661f8ca038e4598730df90165af94dfbf92befcfb9ef813f0`，20,600 B；后续注释 join32 不增加新的原生验证。

- 面积 **208335.5 µm²**，满足 ≤215000；包括 TAP，不包括 filler/decap/macros。
- HPWL **1286480.1 µm**；常规 NETS 信号线长 **1657492.2 µm**；via **299327**，PG 排除。相对 run16 四项均改善。
- maximal 的 272 类零违例，加同一最终 GDS 上独立、非空的 LU.a/LU.b 覆盖；26 个受保护文件不变。
- 时序覆盖 TT/SS × native/OpenRCX nominal，传播时钟、f64、CPPR off；每角 156 个未约束端点，active/input 为 0。相同 SPEF 上 RustSTA/OpenSTA 最大差 **0.392608922361 ps**。
- 这不是 900 s 联合目标通过：**900/750/500 s 仍未完成**。不得把 C7-only 的 887 s 与 T1 的面积拼成一个不存在的结果。

## 本轮 P1 大设计诊断：新的关键发现

已使用合格的诊断引擎 `8ce26e92f6b48901e2aa54d7b11b2c20aa3327fab53d423ec4564c47f4c52b34`（14,071,144 B）运行两次真实 Ibex 诊断。它位于 `W/p1-stage-diagnostic-build04/frozen/bin/rustroad`。

两份原始工作区都保留 HEAD=rev2；在连续共享锁下读取历史 rev1 的五个文件，各自逐字节复制到独立工作区，派生 HEAD=rev1。配置分别为 **24 iterations / 96 evaluations** 和 **200 / 512**，均为 4 workers、4 backtracks、density=2000。旧计划里“6/24”的注释是过时的，不能作为真实配置。

本轮运行和核验：

- Root 授权：`W/p1-ibex-root100/LAUNCH_AUTHORIZED100.json`。
- 真实输出：`W/p1-ibex-stage-wrapper01/future-run01/RESULT.json`。
- 两个直接 Rust PID：**1027042**（24 轮，3.539488461 s）与 **1027090**（200 轮，13.231388476 s）；均实际 wait/reap、exit0、管道排空、运行范围 quiet。直接调用时间仅是诊断进程时间，不是全流程提速。
- 外层完成记录：`W/p1-ibex-root-launch-plan01/outer01/EXTERIOR_COMPLETION.json`；外/内 scope 均 quiet。内层整体 GNU wall 114.73 s，外层 115.63 s；包含诊断和 Python 数据核验，不能用 16.77 s 替代它。
- 独立 b494 核验：`W/p1-ibex-observers101/{iter24,iter200}-GEOMETRY.json`；两次各 46.24 s，实际 exit0/quiet，保留 1 GiB/512 MiB/180 s 和原有全部界限。
- 根几何验收：`W/P1_IBEX_NATIVE100_GEOMETRY_CLOSED103.json`。每例 **14 个位置集、3 个阶段**；全部已报告的 native cost 与独立重算一致，固定对象不变；合法阶段通过检查。观察文档各 3,114,649 nodes，source 1,960,237；合计约 5.075 M <6.06 M，未提高上限。实际 free intervals=6943。
- 原始工作区/副本/原生输出和命令谱系独立复核通过：`W/p1-ibex-native100-peer01/actual01/READY.json`（SHA `ebbf9c10b71372d34d5b8c4666468d2d6707199bc4d66c0993f123ae2e78bba7`）。六个连续SH锁，274,563,352 B流式read/SHA，GNU13.72s、RSS20,608KiB，实际0/quiet。
- 最终 join：`W/P1_IBEX_NATIVE100_FINAL_JOIN104.json`，SHA `d3a88ac968bc7d9dd14d7bb09d82912dd8ef03adc2547f4680ab2efdbfedb10f`，2590 B；把独立谱系与独立几何合并，不增加原生调用。

| 中心 HPWL（DBU） | 24 轮 | 200 轮 |
|---|---:|---:|
| 导入源 baseline | 1275192570 | 1275192570 |
| stage2 after_retarget | 1048684800 | 715059856 |
| stage2 after_legalize | 1145063780 | 1191221840 |
| ordinary_selected / 最终 after_detail | **1113014660** | **1158164430** |

200 轮最终中心 HPWL 比 24 轮增加 **4.0565296777%**；物理引脚 HPWL 增加 **3.9372786238%**（twice-DBU 原值：2245267060 →2333669480）。200 轮 stage2 的 retarget→legalize 中心 HPWL 增加 **66.5905070750%**。这是下一轮优先调查合法化/目标实现损失的实测依据；继续单纯增加迭代不能当作改善。

未赋方向的 projected/retargeted target 只有中心指标，物理引脚 HPWL 明确为 `unchecked_without_orientation`。区间合法性不等于 tap/reservation 完整性，也不等于 DRC/LVS/STA/16 门或新全流程成功。未重跑旧 OpenROAD 比较。

P1 源码与构建：

- 工作树 `modules/RustROAD-p1-stage-diag`；最终 source ready：`W/p1-output-base-fix-source01/SOURCE_READY.json`，SHA `4748617c4c2a36c4aa3344d43de34c80de68238a7ce6c9cea382c6e33d1f14c3`。
- BUILD04 完整记录：`W/P1_BUILD04_CLOSED80.json`。469 个普通测试 +1 个必跑 sparse 边界测试；11 ignored 中 6 个是内存规模测试、5 个是真实 PDK/corpus，不要全部写成 reference tests。
- 新 40 项 tiny 原生矩阵（26 positive、14 negative）及双 PDK b494 几何已关闭：`W/P1_BUILD04_TINY_COMPONENT_CLOSED88.json`、`W/P1_BUILD04_TINY_FINAL_JOIN89.json`。
- 历史 native68 的第 28 项失败保持失败；不得用新的成功改写旧失败目录。BUILD04 只修复输出 base revision 检查等已评审内容，普通计算路径保持源码还原证明。

## 本轮 maximal 独立内存实验 S1

目的是验证完整 maximal 在 **4352 MiB RAM /512 MiB swap /900 s**、原有 16 CPU affinity、固定 2 threads、NoAS 下能否完成，为后续 B4 资源分组提供实测依据。它不是新的联合全流程。

- Root 当前 610 份输入预检：`W/joint-maximal-root99/READY.json`，逐字节核验 **1,574,931,524 B** 输入及实际 Python/GNU，持原始 workspace SH 锁，2 GiB/512 MiB/60 s，实际0/quiet。
- 探针冻结：`W/joint-maximal-standalone-probe01/probe02.py`，SHA `d220c6b2806d76756c8481d91db9df0c1c7d7f091331008302eeb4fedb06900b`；计划 `INPUTS_PLAN02.json`。
- 外部守护：`W/joint-maximal-external-guardian01/guardian05.py`；外层操作器：`W/joint-maximal-root-operator01/operate.py`。源码分别有 29 个 pure +2 个真实 Python-only OS controls、17 个外层 pure controls，以及独立 source peer。
- 实际授权/注册表：`W/joint-maximal-root102/{PROBE_AUTHORIZED102,REGISTRY102,REQUEST_AUTHORIZED102}.json`。
- 主输出：`W/joint-maximal-standalone-probe01/future-run02/`；guard：`W/joint-maximal-external-guardian01/future-s1-guard01/`；完整 GNU 计时：`W/joint-maximal-root102/WHOLE_E2E102.time`。
- 启动于 2026-10-31 14:01:20 UTC。**原生/guardian/controller均0，两个scope均quiet，但最外层操作器exit2**：它在读取完成后的 `RESOURCE.json` 时错误地要求readonly，而合格guard保存的原始资源记录为0644。因此没有原操作器的 `OUTER_RESULT.json`，不可声称干净的全operator成功。
- Root只做具名、同FD/最终identity及当前unit状态的只读收尾恢复，没有改模式、覆盖失败或重跑计算：`W/joint-maximal-root105/ROOT_POSTFLIGHT_RECOVERY105.json`；原exit2保留于同目录 `EXTERIOR_FAILURE_PRESERVED105.json` 及原GNU记录。
- 独立审计：`W/joint-maximal-actual-peer01/actual01/READY.json`、`REVIEW.json`，核对610份原始+私有文件两遍、解析完整原始XML。34项source controls后，由Root106单独授权实际4GiB/512MiB/180s审计，实际0/quiet，5.045230943s，GNU4.26s/RSS26,664KiB，12,605,979,729 B read/SHA；没有重跑EDA。
- 最终资源组件验收：**`W/MAXIMAL_S1_COMPONENT_CLOSED107.json`**，SHA `6b67208a67adb042900166527b6ce47e66613598e8c742c833515e51ef85ed7d`，3321 B。完整272类零违例，XML46,951B/1102nodes；新LU.a，未新执行LU.b。

| S1实测 | 值 |
|---|---:|
| 私有复制阶段 | 8.994499237 s |
| 原生Python wrapper（直接PID1029449） | 410.429170309 s |
| guardian完整wall | 422.283575029 s |
| controller完整wall | 423.370557249 s |
| guardian GNU / exit | 421.51 s /0 |
| 原最外层 GNU / exit | **423.54 s /2，保持失败** |
| cgroup峰值（含文件缓存） | **2,796,642,304 B，约2.60GiB** |
| guardian GNU reaped RSS | 1,069,104 KiB |
| 原生启动前实际memory.current | 20,324,352 B |

原生运行无OOM；421个swap.current采样最大0，不能声称连续swap.peak实测0。独立收尾恢复和审计单独计时，不能把这次失败的最外层时钟当作新完整全流程成功或与历史409s纯native作直接提速比较。下一轮先修最外层对**精确raw资源角色**的模式检查，并做小型metadata/OS控制；不需要为了修报告重跑这次maximal。

探针只对自己复制的文件 fsync/FADV_DONTNEED，不操作原始源的缓存、不全局 drop cache，hint 不计作实际回收。原生启动前须实际 `memory.current <=975205308 B`，再加完整私有文件缓存 1,574,931,524 B、1792 MiB 原生工程余量与128 MiB controller，满足4352 MiB。仍须实际无 OOM、完整272 XML独立复核和内外scope quiet。

**guardian05 是唯一的 `.work/scale.lock` 持有者，不要再外套 flock。** 历史 INPUTS_PLAN02 的 argv 仍写旧 probe.py，属于非授权的旧建议；真正操作必须按冻结 registry 固定 probe02。完整 maximal 272 类包含 LU.a，**不包含 LU.b**；旧同 GDS 的 main 和独立 LU.a/LU.b 证据单独保留，不能宣称 S1 新执行了 LU.b。

## CUDA G1：当前资格与剩余门

当前只完成独立 ABI/compiler feasibility：`W/g1-abi-atomic-root95/COMPILER_FEASIBILITY_CLOSED97.json`，SHA `3068c5229af72a4d9363bdb5948c204f659e24e87e8ab26425ebfd51708e944f`。

- 两个干净、不同 target、相同 crate 路径的实际 cargo-oxide 调用均 exit0/reap/quiet；PTX 同 SHA `ce4502ea4fc33a01a767d262b29b07e083b43008ffcb70e8ad9c9a30b3b951d8`，6987 B。
- 实际10×u64=80 B 参数 ABI，native `atom.relaxed.sys.global.or.b64`，无 helper/float，声明 local=8 B；不是完整 G1 kernel 的资源属性。
- 两次编译合计完整 operator wall **134.883155177 s**；reaped RSS 最大571748 KiB；各 worker cgroup peak754782208/754585600 B，swap0。
- **GPU 调用0**。没有完整 G1 几何核/PTX/新 CPU gate/设备差分/生产集成/SPEF/端到端提速资格。不要把这个探针的8 B local 或 equal-PTX 结论转移给完整核。
- 旧79 compiler101、82/92 artifact postflight 失败，以及90/91/96 bookkeeping 失败全部保留。Driver07 对3个生成文件及3个隐藏空目录做完整 namespace 核验后，才有95的两次成功；不能只检查 `ls -l` 可见文件。

完整 Phase B 位于隔离工作树 `modules/RustEDA-Common-g1-cuda-phase-b`，分支 `codex/ibex-g1-cuda-phase-b`，基于 `a6185e06b43092dd822debd7e3125a67f5e0d76e`。只有 extract agent 修改过该工作树，当前没有主编译或设备执行。

最终只读源码快照：`W/g1-cuda-phase-b-source-round01/SOURCE_READY.json`，SHA `7ab3c97022d474aff88271b79813f7d8fbbaa1fe425828a74dd11a3267cfe151`，4778 B；manifest `b47de8be7d11825d32f5b80e65105f998114a65bb9b4cb681d17352fccf71041`，**347文件/5,150,501B**。详细接续说明：同目录 `CLAUDE_HANDOFF.md`；未启动的独立peer说明：`W/g1-phase-b-peer01/CLAUDE_HANDOFF.md`。

**这些源码没有完成格式化、类型检查、编译或独立source review。** root和adapter两个canonical离线锁文件更新已独立graph核对并发布（`W/g1-cuda-phase-b-host-source01/LOCK_PUBLICATION.json`，`6267943c…`）；GPU子项目仍是88B占位lock，私有离线index缺 `anyhow`，metadata03整组仍失败。使用已有D0缓存解决，不能手工拼GPU lock。`verification/shared.sha256` 当前过时，31-part protocol `14b280e2d2ddaaa3c2bb7d845eba7ed7548e11a0e22e6e38a20c4ddf967b01ac` 只是暂定值，最终lock/source/format后必须重算。

另一个优先阻断项：freezer对旧Device的粗粒度strip还原比较，**byte和whitespace两项均false**。这可能来自比较范围或真实改动，但当前没有还原证明，不能宣称旧Device/default路径已经保持不变。先阅读 `RESTORATION_SCOPE.json`，独立检查CPU/Device默认路径、typed状态、精确IO/error tail和registry，再构建。已有v3_transport的CUDA-unavailable断言也需要按新feature行为检查，不能跳过失败测试。

必须保留的设计：

- 新 Owned driver-only VerifiedPtx/PtxDevice seam；必须补齐旧 Device/NVRTC/default dispatch 还原证明，不能把新核设为默认。
- 一个独占 Budget；host 全量范围证明必须在 CUDA context 创建前完成，并计入同一 work/IO；完整 CPU i128 路径保留，不支持的宽坐标显式回退。
- kernel10u64/80B、device outcome16B `{work:u64,status:u32,tile_local_index:u32}`、host rolling24B `{globalordinal:u64,work:u64,rawstatus:u32,zero:u32}` 是三个不同对象。
- raw reason **0..5 无损**；semantic Invalid/Budget 诊断不能替换原始逐 query status。所有已启动 queries 的 work、首个全局错误、65537 跨 tile 边界、尾部和 activation word63/64 都要进差分与 rolling ledger。
- GPU 整数算术/native u64 OR；不引入GPU i128/f64、不按Q×I物化。local cap256 B/thread ×65536=16 MiB 只是暂定，运行前必须查询实际函数属性和 free VRAM。
- 暂定 standalone runtime **3 GiB/1 GiB swap/NoAS**，工程模型3027189760 B；与 Road 的3.5 GiB共驻留仍 HOLD。
- 已合格 PTX/ELF 的编译器、backend、LLVM 等14个未使用构建 image 是构建来源，不能在3 GiB runtime 里重新读取它们全部后假装缓存免费。runtime 仍核验实际ELF、完整registry、新CPU gate、image manifest、PTX和实际CUDA loader/runtime images；若外部每次重新哈希构建工具，必须单独报告并纳入完整端到端时间。
- CUDA 的 public API header 可核验 ABI/enum；不读参考 EDA 内部实现。实际映射 image 要 join dev/inode，JIT函数 local/register等属性必须实测。

既有 CPU G1 资格独立保留：`W/G1_V3_CPU_COMPONENT_CLOSED47.json`（131次不同PID小型矩阵），`W/G1_V3_REAL_COMPONENTS_CLOSED71.json`（两个真实副本约11.8Mqueries/181prefixes，零 activation 差异，完整IO ledger）。CPU ELF `6b32da4cd3ede00c0a5ae447f4bdd890adf450df61dd93c89d1cb52936dc634f`，1,427,488 B。新源码/协议/ELF 需要新 CPU gate，不能继承旧 gate。

## 剩余工作顺序

1. 先复核本文最终收尾结果、根 HANDOFF 和当前 dirty/worktree 列表；保持所有旧证据不可变。
2. **P1 优先调查合法化损失**：用已核验的24/200阶段输出定位排序、retarget、合法化与detail的目标失真。研究/改算法前估算内存、设硬上限；新 ELF 必须重新做默认路径和独立几何验证。不要直接把200轮作为生产改进。
3. S1的资源组件与独立272 XML已经关闭107，原最外层postflight失败仍需修复。再用它推进 B4 的独立job/slice资源方案：`W/joint-b4-maximal-overlap-plan01/`。当前产品 worker 缺委托子组，不能直接 mkdir child cgroup 或把 scope 上限相加当作 aggregate 证明。LVS/RCX顺序、真实祖先的最小cap、父controller文件缓存和writer均需明确。
4. 时间关键路径分析：`W/joint-t1-criticalpath-analysis01/READY.json`、`report02.html`。联合比C7增加507.4294s，其中tap444.7616s、postroute130.4410s、route减少58.4436s等。即使完全隐藏新增tap，仍约949.9572s；B4单项重叠估算只隐藏约140.575s，不能承诺900。需要再消除至少约49.96s保留路径，或同时优化tap、postroute/route。
5. 完整 G1 source review 后，为其新源码/锁文件/协议制定有界 CPU build与gate、PTX build、运行image资格，再做设备差分。按实际编译结果修复，不要把自写Python转录测试或probe95当作主核正确性。
6. 真正的产品优化完成后，再准备一个新的独立完整联合 flow：充足磁盘、输入拷贝、source/ELF/tool SHA、完整 wall/RSS/cgroup、全128步/16门/QoR/双角时序/独立LU.a/LU.b。未跑就是 unchecked，不发布估算为测量。

## 操作与证据注意事项

- 优先 `./dev`，独立工作树无法通过它选择时用已经评审的有界 harness。重活前 `free -h`/磁盘检查，CPU `CARGO_BUILD_JOBS=1`、测试线程2..4、timeout +cgroup +GNU time；估算>4GiB的序列化。
- 当前可用的 GNU time 不在 `/usr/bin/time`，在 `.work` 的 Root授权/工具FIDs中有完整绝对路径；Python 同样使用指定固定解释器。不要任意替换。
- 原始 workspace HEAD/lock 可有特定0644控制角色；私有lock0600，数据0444。不要全局强制一种mode。lock须同一FD持SH，前后 fstat 与当前具名 inode一致。
- 从实际 JSON 读 schema/字段/路径，再写 adapter。此前猜 `launcher_exit_code`、symlink2字段、旧control目录名等造成过独立的 bookkeeping失败，均已保留；不要重跑重型计算来修报告字段。
- 不将编译日志 `Finished` 当作被保留的 wait0；失败的82/92就没有最终合格 child receipt。只用实际PID/wait/quiet/当前FIDs。
- 当前报告 `W/report.html`、`W/report-data.json`；根状态 `.work/ibex-sg13g2-bringup-20261028/CURRENT_STATE.json`。最终交接前会同步本轮状态；两者是摘要，原始验收JSON是证据依据。

## 本轮最终收尾记录

- P1：Root104最终join已通过，两个native+两个独立几何+独立输入/谱系检查；完整物理/时序/flow仍unchecked。
- S1：Root107资源组件/独立272 XML关闭；原最外层102exit2保留，105是单独的只读postflight恢复。未新执行LU.b/16门。
- G1：347文件只读快照已封存；GPU lock、stale hash、Device还原、格式化、完整peer/构建/CPUgate/PTX/device都留给Claude。没有完整核GPU调用。
- 三个子代理已停止，不会后台继续；Root启动的原生和审计scope已结束。没有新增产品提交或push，原始dirty模块保持。
- 报告与CURRENT_STATE只同步本轮证据范围。若摘要和本文件存在歧义，以Root104/107、独立审计及原始失败记录为准。
