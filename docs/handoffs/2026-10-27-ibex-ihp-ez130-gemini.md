# Ibex / IHP SG13G2 / EZ130：交给 Gemini 的工作交接

记录时间：2026-10-27 19:57 CET（18:57 UTC）。Source15 已失败并退出；接手时重新查询状态。

## 1. 目标与当前结论

用户要求：本机原生安装最新 LibreLane；RustEDA 与 LibreLane 都从固定的 Ibex RTL 开始，使用 IHP SG13G2、EZ130 标准单元和真实 SRAM 宏，完成 RTL2GDS，通过完整 DRC、LVS、提取后时序验证；然后做同配置的端到端 benchmark。

**任务尚未完成。**

| 项目 | 当前真实状态 |
| --- | --- |
| 原生 LibreLane 安装 | 3.0.17，官方最新版本已复核，AppImage/native FUSE，无 Docker |
| LibreLane 全新 RTL2GDS | **fresh04 已完成，10/10 门禁通过，独立审核通过** |
| RustEDA 组件资格 | Source15 / e01 的精确工具组合，13 项组件资格通过 |
| RustEDA 全新 RTL2GDS | **Source15 已失败，69/92 receipts，`be_route` exit2；尚未整芯片合格** |
| 原生完整制造 DRC / 全部天线 / 器件 LVS | 尚未取得这次 Native 新 GDS 的结果 |
| 原生双 SPEF × TT/SS × setup/hold 八值 ≤1 ps | 尚未完成 |
| 正式端到端 benchmark | **0 样本，未计算加速比** |
| 修复落回正式模块 | 应用层相关源码已在 dirty 工作区；引擎累计补丁已准备，尚未统一应用/更新依赖 |

用户最后追加要求清理 RustEDA 下所有 Rust 编译缓存。清理收据和实际释放空间见本文末尾的“缓存清理交接”；不能把审计候选大小当成已释放空间。

## 2. 先读这些文件，遵守现有约束

- [AGENTS.md](/home/cgao/git/RustEDA/AGENTS.md)
- [HANDOFF.md](/home/cgao/git/RustEDA/HANDOFF.md)
- [模块架构](/home/cgao/git/RustEDA/docs/modules.md)
- [详细过程记录](/home/cgao/git/RustEDA/.work/ibex-sg13g2-repair/STATUS.md)
- [HTML 状态报告](/home/cgao/git/RustEDA/.work/ibex-sg13g2-ez130/report.html)

关键规则：

1. **Clean-room**：参考工具只作为外部黑盒，通过文件格式、CLI、运行时间、RSS 和可观察结果比较；不得阅读、diff、翻译参考引擎源码来修复差异。
2. 不新建根 Cargo workspace；模块历史独立。保留 dirty 品牌改动、归档和 Vivado 实验 worktree。
3. 不把编译通过、局部测试或历史 GDS 门禁当成新整芯片通过。未检查指标保持 `null` / `unchecked`。
4. 重构/重跑先估算内存，检查 `free -h`，采用内存与墙钟上限、`CARGO_BUILD_JOBS=2` / `RUST_TEST_THREADS=2`；大于 4 GiB 的重任务串行使用项目 scale lock。
5. **公开 `./rusteda flow run` 自己取得 scale lock 和 worker scope；不能在外层再包 flock 或 systemd scope。** 否则可能死锁/错误预算。
6. 最新 Native 已退出失败，所有冻结代码、配置、helper、工具、PDK绑定和失败产物继续保留；后续候选使用独立新目录，避免覆盖证据或出现第二个 writer。
7. 大的失败快照约 240 MB。读头使用 `open(...).read(65536)`，不能 `read_bytes()[:65536]`，后者会先整文件载入内存。

项目根目录 `/home/cgao/git/RustEDA`；本任务根目录 `/home/cgao/git/RustEDA/.work/ibex-sg13g2-repair`，下文简称 `W`。

## 3. 接手第一件事：核对 Native Source15 的最新失败终态

最新完整 Native 尝试已经失败，session48691 已消费 exit1，scope inactive，cleanup verified。**保留它，不要在旧工作区重新从 rev0 启动。**

| 字段 | 值 |
| --- | --- |
| 准备/冻结目录 | `W/synth-agent/native-h4s2o1-prepare01/finalized15` |
| 公开工作区 | 同目录的 `public-workspace` |
| 实际输出 | `public-workspace/steps/rtl2gds/attempt1/output` |
| request ID | `40bb1326886aae232f2e757e00242a3e962dccf4309d0a3571e5830d63fa1e14` |
| **实际 systemd unit** | **`rusteda-worker-40bb1326886aae232f2e757e.scope`** |
| Codex shell session | `48691` 已消费 exit1；Gemini直接用文件和systemctl查询 |
| 历史 RustROAD PID | `4002762`，任务已结束，不能据此PID执行进程操作 |
| 启动记录 | `W/root-native-fresh-final15-run01/launch.json`、`run.sh`、`public-flow.stdout`、`public-flow.stderr`、`public-flow.time` |
| 观测脚本/记录 | 同目录 `snapshot.py` / `monitor.jsonl`；脚本只读取状态并向自己的 monitor 文件追加一条记录 |
| 公开工作区终态 | `rev2`，failed；不要与producer工作区HEAD混同 |

可先执行：

```bash
cd /home/cgao/git/RustEDA
python3 .work/ibex-sg13g2-repair/root-native-fresh-final15-run01/snapshot.py
systemctl --user show rusteda-worker-40bb1326886aae232f2e757e.scope \
  -p ActiveState -p SubState -p MemoryCurrent -p MemoryPeak \
  -p MemoryMax -p MemorySwapMax -p AllowedCPUs
free -h
df -h /home/cgao/git/RustEDA
```

**unit 必须带 `.scope`。** request 的短 unit 名省略后缀；直接查询不带后缀的名字会命中不存在的 `.service`，误判为 inactive。

失败前的19:52历史观测（仅说明轨迹，后面的终态优先）：

- 68/92 前缀完成：66 个 command exit0，加 2 个 render。render 没有 `exit_code` 字段。
- 最后完成 `be_profiles`，`be_route` 运行中；最近日志到 round822 的 alternate-root retry。
- 初始 detailed 阶段：1072 个待修网络，工作 `9,287,427,525`，扩展 `147,378,925`。
- 最近整百轮：round800、queue2272、累计工作 `73,564,293,412`、扩展 `279,274,829`、工具 RSS550 MiB。
- scope 峰值 `1,219,481,600` B，8 GiB RAM / 2 GiB swap / 固定16个不同物理核；尚未观察到资源上限终止。
- 队列会因拆线加入受影响网络，**不能由轮数/队列推算完成百分比或剩余时间**。
- 没有完整新路由提交、整芯片 DRC/LVS/timing pass 或 Native qualification。

固定预算：whole21600s、route7200s、比较工作200B、扩展2B、单次搜索8M、活跃节点200k，原规则和上限均未放宽。环境明确设置 `RUSTROAD_ROUTE_TRACE=1`，其日志 I/O 计入运行；后续 Native benchmark 也用同一策略。

已在独立512MiB/128MiB/60s受限scope生成 `rev2` 公开失败报告，命令exit0仅代表报告读取成功，不是设计通过。入口：[终态封存](/home/cgao/git/RustEDA/.work/ibex-sg13g2-repair/root-native-final15-terminal01/READY.json)，SHA `b7426d7b837698175da5a263b5a6a7d336b997564bf570be4cd6dd0de0edd54f`。报告 `public-report.json` SHA `0527a2643387426c0d78cb76a0b05382bbd209ff7e133f0b6161b55ea4306595`。后续新候选的报告仍必须取它自己的实际安全终态：

```bash
./rusteda flow --workspace <finalized15/public-workspace绝对路径> \
  --tools <finalized15/public-toolchain.json绝对路径> \
  report --rev <实际安全终态rev>
```

如果失败：保存当前终态、全部 receipts、资源/清理回执和实际失败 witness；不能原样盲重试、重置累计 ledger 或直接提高预算。局部诊断用新有限预算，并明确它不是失败调用原 frontier 的重放。

### Source15 最新实际失败（接手优先级最高）

- 69/92 producer receipts，68前缀成功＋`be_route` exit2；公开flow exit1 / `WORKER_FAILED` / `complete=false` / qualification null。
- **round822 / net13565 / failed_pin46016**，`BUDGET_EXCEEDED: per-search detailed limit after bounded root retries`；日志已有 alternate-root retry，随后 `PostFailure` capture。本轮尚未解析全累计ledger，不得写成200B耗尽。
- be_route1810.361865869s；公开记录worker1838.191134005s；producer1837.697522702s；外层GNU elapsed30:44.80。max-process RSS565104KiB；cgroup peak1219481600B / swap0。timed_out/cancelled/log_limit_exceeded均false；cleanup verified、scope inactive、containers absent。
- 新快照：`finalized15/public-workspace/steps/rtl2gds/attempt1/output/backend/route-budget-error.json`，**232713330B / SHA `71203261258a4670faa4d98eb9670fb565af2148cd088992db8d953bdbdf3941`**，schema rustroad.route-replay.v1、phase post_failure、strict false、terminal_order `[80493,30101,30136,30143,46016,30223]`。不是旧Source10快照，不能当作失败调用的原frontier。
- root只对终态/receipts/logs/snapshot做流式hash并生成公开失败报告，没有解码大snapshot或跑新EDA，也没有声称全源重新资格。16门禁中的整芯片部分仍未验证，benchmark仍0。


## 4. 冻结的设计与公平比较条件

| 条件 | 固定值 |
| --- | --- |
| Ibex | v2.1.0，`38c070939183cc10940b66c8e9e04eeca6b65470`，RV32IMC、FF regfile、无 cache |
| top | `ibex_sram_bench` |
| 原始 RTL 转换结果 | SHA256 `8a8fe84b181e6e3b169721034850a1b8fabfd320d988eb89e016598e906911e4`，440170B |
| PDK | IHP SG13G2 v0.3.0 / `5cccb161f7492697cfa52eb14dc03beb00bdca9e`，`/home/cgao/pdks/ihp-open-pdk/ihp-sg13g2` |
| 标准单元 | `/home/cgao/pdks/ez130_8t_v1p1` |
| SRAM | **2 × `RM_IHPSG13_1P_1024x32_c2_bm_bist`**，各4KiB，原厂 GDS/LEF/Liberty/CDL/模型；不能展平成 FF/伪宏 |
| die / core | die1100×850µm；core(20.16,20.16)..(1079.82,829.92)µm |
| 宏位置 | u_imem(80,430)、u_dmem(600,430)，N，10µm halo |
| 网格 | .42×3.36µm site；1000 DBU/µm；5nm grid |
| 时钟/IO | 40ns，IO8ns，uncertainty.25ns，transition.15ns，load.006pF，BUFX4 driver，maxfanout10 |
| 时序角 | TT1.20V25°C / SS1.08V125°C；early.95/late1.05；nominalRC；CPPR off |
| 信号/时钟层 | M2..M5；M1 pin access；原 top PG；真实 filler/DECAP/tap 保留，tapgap15µm |
| 资源 | CPU `[0,2,4,6,8,10,12,14,16,17,18,19,20,21,22,23]`；8GiB RAM / 2GiB swap |

SDC SHA `5f0551e15fe0a0fa8490b213329c0a5819ea5cc03a2ba93f621de22d350090f6`。
共同比较合同 SHA `e57e603ca192b24b6170fa4f88501c67057f61b372d4fbed87143a9a53d5db2d`。

RC 必须使用原始 `libs.tech/librelane/openrcx/IHP_rcx_patterns.rules`，SHA `ffda02d57a6ecb39b02268be9511d89ef072911f17bd2dcd095f3f3cbeb5a905`，50282B，**显式 OpenRCX v1**。Magic 规则或 v2 不是这次匹配模型。Native nominal RC profile SHA `56e4933c96d4855d1de600634db5f63df9adb462811cb59ef37f0a15a1538bed`。

## 5. 当前合格工具组合与 Source15

| 工具 | SHA256 / 路径入口 |
| --- | --- |
| RustSys | `e6cf0c7787fe5e7f4f1b7879f088554f6a665296fc9b5f3a2263bdb2f0a604d5`；`W/synth-agent/binaries/<SHA>/rustsys` |
| Common CLI | `af6b7ade0cd9dc7a1e9204af85fb9db2a2f34b6bc92b3e65fcf0c3934d15ac0f`；`W/sta-agent/bin/<SHA>/rusteda` |
| RustSTA | **`b17c092b2e180ea286c433fb0f979f30ffe749f6ccff3f948e3c957c227e024c`**；`W/sta-agent/bin/<SHA>/ruststa` |
| RustROAD | **`e01ce2637d2f144756649e3fe33d641d0f1c7013a6587243c1c3edcc1dac847e`**；`W/road-agent/bin/<SHA>/rustroad` |
| 公共 App | `154f936e4a39f27c4fbab3bdf208fa50fbc828e9b774c02f5c0e8cb38c04b6ae`；根 `./rusteda` 调用相应固定工具 |
| LibreLane | 3.0.17，`/home/cgao/tools/librelane/3.0.17/librelane-devshell-x86_64.AppImage`；SHA `c8ef5ae26b1fc53dd1dd30fe2f1a1da6ab0fdf471dd5a94c2b85d2b3db36833c` |

LibreLane wrapper `/home/cgao/bin/librelane`。官方 `/releases/latest` 在2026-10-27T17:55:01UTC仍指向 [3.0.17](https://github.com/librelane/librelane/releases/tag/3.0.17)；证据 `W/librelane-latest-recheck04.json`。

Source15 在已合格 Source10 基础上只改4个源文件：新增可选 `single_owner_repair.heuristic_weight`，范围1..16；None 保留旧行为/序列化字节；显式 owner 权重只在受限 trial 内更具体，所有成功/失败/预算返回前恢复外层设置。

本次生产策略：**硬搜索4 / 普通软搜索2 / 受限单 owner1**。仅改算法策略，不改物理规则、SDC、ledger 或资源上限。

- [OWNER_READY15](/home/cgao/git/RustEDA/.work/ibex-sg13g2-repair/road-agent/owner-weight-source15/OWNER_READY15.json)，SHA `c5a4af3574f836257237b889d22c5bdea84c4bb7fc7b97c45a3575b4e437807c`。
- manifest15 SHA `b91e78da1e0b363564049bd0e717dcb703037a5f137532d9fac657fbe79412be`；554个冻结源文件。
- 测试：360通过、11既有ignore；严格 Clippy/fmt/docs；14个真实保存状态控制；2/16线程确定性；双PDK正常 H4/S2/O1 小流程通过。
- 注意旧 replay 的 `--heuristic-weight2` 也会将硬 scout 改成2，所以这些 replay 是 **H2/S2/O1**，不能冒称生产 H4 轨迹。
- 精确13项组件资格：[qualification.json](/home/cgao/git/RustEDA/.work/ibex-sg13g2-repair/synth-agent/component-qualification15/qualified-final15/qualification.json)，SHA `63ee8828b4440377559a0203f1acf5b8bcc099f6a19554e1a89e84159ae51d03`。
- 当前 public manifest SHA `715eef9eb2411d932eae0309250373c9eeca9c9a23ccbbfea6c827b3f02e2f3b`；finalized15 READY SHA `fc21ac1429303449c6524bb8f323a1a0e76d28a3a2975a743304c039b925ad63`。

组件资格包含宏/总线/PG/短路/井接触/天线等正负控，不等于整芯片签核。

## 6. LibreLane fresh04：已真正完成的结果

目录 `W/reference-fresh-qualification04`。从原始 RTL 全新运行，未借用历史 GDS 门禁。

- [最终资格](/home/cgao/git/RustEDA/.work/ibex-sg13g2-repair/reference-fresh-qualification04/qualification/qualification.json)：SHA `7474264162190c48dc227097b8dc18b7b2913613d32d4891c7c1300dd201e338`，43736B。
- `complete/end_to_end_qualified/benchmark_eligible=true`，failed为空，10项全通过。
- 实际44步（含qualify）；运行绑定记录的是qualify前43步。
- 原125输入+11工具=136 FileID、46源文件冻结均独立重核。
- 完整 IHP main DRC **0**（222.35s）；原始全部31类天线 **0**（144.68s）；完整器件级 LVS **通过**（130.63s），134电路均匹配、122端口、双真实宏及5类器件匹配。
- 原厂双 SRAM 程序仿真通过。最终 filler/DECAP 与原厂宏保留。
- 全新过程中仅对**当前 LVS/连线证据证明的两个孤立被动 A8 单元**做保线清理，随后正常 Fill、XOR、重新导出及全部新门禁；不能推广为任意删除天线单元/填充器。

当前合格 GDS：
`W/reference-fresh-qualification04/runs/floating-1-sealed/final/gds/ibex_sram_bench.gds`

SHA `43545d95068df14ab5f0a986875ac0b26637dcecf78ff90c447d71ac89706620`，22203746B。

最终 netlist SHA `693d6c5f1a0027476c7f97f41b0443039320ce5c32d0a2710e3c340ad8a4430e`；powered netlist `0f5ce26a2b511be3f82900923533d19333fe987552fd4d991e32f364921a56d5`。

两个实际 SPEF 的 OpenSTA 最差裕量（ns）：

| 提取来源 | TT setup / hold | SS setup / hold |
| --- | --- | --- |
| LibreLane SPEF `57852f54…` | 13.229065895081 / .118687063456 | 1.145110726357 / .344312369823 |
| 独立 v1 OpenRCX `20710f57…` | 13.237489700317 / .119100399315 | 1.152880549431 / .344645202160 |

两份的绝对时序、DRV与有效寄生覆盖通过。该**参考版图**同 SPEF 的 RustSTA/OpenSTA 四值最大差分别 .611082515 / .710066747 ps；**不是 Native 版图双提取八值资格**。

时序范围要诚实：156恒值禁用端点逐项有OpenSTA witness；162为另一套DRV恒值driver排除计数，不得混同/相减；125实际无连接输出有证据。1633未被时钟覆盖的pulse检查仍unchecked；crosstalk grounded factor1；全四状态形式等价未验证。

整体资格运行3242.298465622s，GNU max-process RSS3,310,988KiB，cgroup peak6,144,299,008B，swap0，scope已清理。**这是资格运行耗时，尚无正式 benchmark 比值。**

独立终态审计：[READY.json](/home/cgao/git/RustEDA/.work/ibex-sg13g2-repair/synth-agent/reference04-terminal-audit01/READY.json)，SHA `7a8eb422ff2ed706d7aca400b1b8cd4bc603a1cd2a09eb9ff4d98804cfd775b5`；469 FileID重核。计数范围另见同目录 `COUNT-SCOPES.json`。

## 7. 已准备但尚未统一落地的产品源码

**不要以“私有固定 ELF 跑通”冒称所有正式模块已更新。** 当前 live 模块及品牌 dirty 都保留；尚无本任务新增 commit/push/根gitlink更新。

| 模块/层 | 已完成的只读准备 |
| --- | --- |
| Apps | App06产品源码175/175与live字节相同，相关修复已经是现有dirty；构建专用`.cargo/config.toml`与lock差异另列 |
| RustSys | 15个Rust文件累计补丁及独立文档补丁，对live `git apply --check`通过，未apply |
| Common | 32路径累计补丁；真实修复尚未进live |
| RustSTA | 46路径；code-only45与本地path Cargo.lock分开；修复尚未进live |
| RustROAD | 精确Source15累计65路径；补足Source06基础后再接18-file Source10与4-file Source15；对live检查通过，未apply |
| Reference helper | fresh04冻结46项中，H相同43项；`reference_ecos.py`、`reference_flow.py`需更新，缺`physical_lvs_observer.py`；还依赖外部13个明确FileID，不能只复制3文件就称产品化完成 |

入口：

- [Sys / Apps 落地与 benchmark 准备](/home/cgao/git/RustEDA/.work/ibex-sg13g2-repair/synth-agent/source15-readonly-closeout01/READY.json)，SHA `57dfcddec42e1153c3f8b452ec33749ee255cbad96326c2af0fd3c1320dad069`。
- [Road 集成准备](/home/cgao/git/RustEDA/.work/ibex-sg13g2-repair/road-agent/source15-integration-prep01/README.md)；READY SHA `3a49183f6d3836c40449df7e69bfb79b67f52bf99203b175639badf80bf46f16`。
- Road exact累计补丁 SHA `f3e66a7ed6cf9a6cf413d12edf309274dcd42021b847bb4f97975b54a570d11e`，685983B；另有含runtime RC helper的独立候选。
- Road冻结ELF源码的 `scripts/build_rc_profile.py`是23f2…，实际runtime已审helper为30442713…，新增明确corner/calibration规则/provenance；必须区分ELF源与runtime helper的身份。
- [Common/STA与Reference helper落地方案](/home/cgao/git/RustEDA/.work/ibex-sg13g2-repair/sta-agent/product-promotion-audit01/PROMOTION.md)；READY SHA `b9af10f5a5600ca6ed0304a44ba92c6793afedd9cd8f83c04254af807e1f4a8c`。
- 同目录 [EXTERNAL_HELPERS.json](/home/cgao/git/RustEDA/.work/ibex-sg13g2-repair/sta-agent/product-promotion-audit01/EXTERNAL_HELPERS.json)，SHA `f2eb00c8fdcb65a4c1dffaad22b5b7cf6d7c5c6436321904cf0e3ec76541b827`，列13个外部依赖；精确文件范围见 `SCOPE.md`。
- [Road排序只读结论](/home/cgao/git/RustEDA/.work/ibex-sg13g2-repair/road-agent/source15-net-order-readonly.md)：global升序GCell HPWL、detail升序guide.len目前写死，没有现成net-order配置；large-first仅是未验证假说，未实现或运行，不能据队列增长宣称必不收敛。

**依赖落地是实际剩余工作：** live RustSTA/Common当前仍固定旧Common `c96…`。`./dev`只运行`cargo … --workspace --locked`，不会自动注入本地Common patch。单纯apply源码后可能仍解析旧Git依赖。

正式可发布顺序：Common精确改动形成可达reviewed/draft子分支提交 → 所有consumer更新Common rev和正确Git lock → 引擎相应子分支/验证 → 子提交先push到可达分支 → 再发布根gitlink更新。不能顺手merge child main；保留品牌/其他实验。仅本地验证时可使用独立CARGO_HOME配置本地五crate patch及匹配lock，但这不是正式Git依赖方案；`./dev shared`会拒绝local-path锁。

新构建二进制的SHA若改变，须明确新旧身份并重新绑定适当资格；不能把e01/b17的资格标签直接贴到别的ELF。当前benchmark应始终使用经过精确资格的冻结组合。

## 8. 不要重复的失败/拒绝方案

### Source10 整体路由预算失败

旧工具1bb、旧request1493679、`W/synth-agent/native-fresh-owner-prepare01/finalized04`：69/92 receipts，be_route exit2/3377.289s，whole约3409.876s；未完成路由，无签核。

失败发生round844/net17296，实际累计算账200B−1，扩展约512M；不是OOM、超时或日志上限。终态封存 `W/synth-agent/native-final10-terminal-seal01/READY.json`，SHA `a0eeb568dc7a7d8f4614eda898112424e003f2cba1834b16dbbf98b878f53a13`。

约240MB `route-budget-error.json`是post-budget状态，含当前未提交网络状态，**不是原失败调用的frontier/合法完成checkpoint**。

### 路由优化试验

精确目标距离缓存、空间查询缓存、目标containment都做过真实状态费用比较，净收益不足或反增；**Source15不包含它们**。不能因命中率/减少目标数就声称端到端更快。

普通soft权重2在两个真实状态减少约64%/87%工作，但受限owner权重2会增费并使一个可路由控制失败，所以分离成H4/S2/O1。代价是路线/被拆网络集合改变，必须等当前whole run。

### RustSTA F1 波形研究已拒绝生产

私有F1修复通过局部Ngspice物理控制，却在Sky/GF及历史实际SPEF对照中明显变差；ELF `2f4d355e…` **不能替换生产b17**。报告 `W/sta-agent/full-slew-f1-source01/RESEARCH_RESULT.json`。历史GF7强控制中原b17也有1.706690ps反例，不能宣称任意设计全局1ps精度；当前门槛针对实际待验八值，不能放宽以求过关。

## 9. Native 完成后的签核与 benchmark 顺序

### A. 先完成 Native 整芯片资格

必须取得同一实际新运行、同一最终GDS/网表/提取链绑定的16项：

`antenna, controls, cts, drc, extraction, final_equivalence, inputs_frozen, lvs, macros, placement, power_connectivity, routing, simulation, synthesis_equivalence, timing, timing_accuracy`。

要求完整原始main DRC、原始31天线、full-device LVS、原厂双宏仿真、逻辑独立证明、实际有效寄生覆盖/DRV、**2真实SPEF × TT/SS × setup/hold至少8值，RustSTA/OpenSTA最大绝对差≤.001ns**，CPPR off。原始8376个两状态逻辑义务已在前端通过；这不证明全四状态形式等价。

确认92步command均exit0（render单独处理）、公开`rustlane.v1`报告ok且complete/qualification_status=passed、相同request、cleanup完成、scope inactive、无残余后代、非cancel/timeout/log-limit、原冻结输入未变。建议独立重核实际门禁和FileID，不只读汇总布尔值。

### B. 然后冻结真正的 benchmark 配置

既有严格执行器不必重写：

- `W/synth-agent/benchmark05-private01/proposal.py` SHA `44f17da055fee0228217a7a7d06bea6cd6acd45b911e78ba3d777044f81212f9`。
- 同目录 `executor.py` SHA `4980b7c632c69a0e7a9afe30966226b2ef75393b8f79ca482553ea4ea6a976b2`。
- 新候选draft：`W/synth-agent/benchmark05-native15-draft03/READY.json`，SHA `f569722fb03818f88cb75baeb1d49ed21502aa650d1d6858b6eaa8fab184c078`；406records/38receipts。
- [严格收尾计划](/home/cgao/git/RustEDA/.work/ibex-sg13g2-repair/synth-agent/source15-readonly-closeout01/benchmark-finalization-plan.json)，含字段与未来实际命令。

draft仅填了真实Reference747426，**Native qualification/public report、final freeze、executor freeze目前为空**，inspect blocked。不能改名为final/捏造pass。

**当前draft03绑定已失败的Source15，不能直接填入另一个候选的成功报告。** 若修复改变工具、策略、helper或manifest，先在新目录更新相应template、精确Q及source绑定，重新生成匹配的新draft。既有finalization-plan针对Source15成功的具体路径分支现在只是schema/流程参考。新候选真正成功后，再填入它自己的真实终态报告并重核所有模板/工具/helper/profile/Q/source receipts；final release严格键`schema/status/records/source_receipts`，`rusteda.benchmark-freeze.v1`，records≤512、receipts≤64。运行frozen proposal plan与executor inspect，必须`admitted_not_executed`且blockers空，方能启动。

### C. 最后才做四次串行 ABBA

每个样本都从原始RTL fresh prepare开始；外层计时在输入hash/prepare/SV转换前开始，安装/编译工具排除。同合同、同16物理核/8G2G/SDC/真实宏/规则/日志策略。不得复制旧design state、拿局部route replay算E2E，或失败自动重试。

公开Native样本不能外层重复flock/systemd；pair层仅benchmark.lock。任一样本失败保留证据、对比标incomplete、不算ratio。分别报告max-process RSS与cgroup peak，不混用为单一RAM比值。

容量要以**完成后的Native实际目录**与Reference4.936GiB重新估算；当前Native约2.333GiB仍增长。若最终Native4/6/8GiB，四样本加5GiB余量的规划约需22.87/26.87/30.87GiB新空闲（还要计当前Native后续增长及其他进程写入）。这些是规划情景，不是硬上界。清缓存后仍须重新测量，不能只看四舍五入的`df -h`。

## 10. 交付前还要补齐

1. **先修复Source15实际失败：round822 / net13565 / pin46016的单次详细搜索上限。** 按结构化witness有界诊断，不能认作旧200B耗尽；修复后精确组件资格、新冻结与全新公开运行。
2. Native实际全部16门禁及双提取八值时序，独立终态审核。
3. 修复统一落回产品模块，处理Common Git依赖、helper产品化、正确source/tool identity与必要回归；保护所有dirty历史。
4. 两条真实新资格通过后，最终freeze与inspect admission；四次fresh ABBA；QoR/时序/资源/耗时一起报告。
5. 更新HTML、HANDOFF、STATUS和本交接；所有未检查范围保持明确。没有全通过前，不宣称用户目标已完成。

## 11. 缓存清理交接与最后更新

用户在本次交接前明确授权清理**RustEDA目录内所有Rust编译缓存**。范围限`/home/cgao/git/RustEDA`，不清用户全局Cargo目录；保留源码、Git/归档/源快照、历史运行输出、实际工具/运行库及冻结证据，保护活动进程引用。用户新授权包含legacy/experiments下确为可重建的编译缓存，不表示这些目录整体可删。

19:51 清理前可用空间约15.81GiB（16,974,909,440B），只有root ext4数据盘；另一个NVMe为未挂载BitLocker，不应改动。此时唯一检测到的RustEDA引擎进程为上述RustROAD，无cargo/rustc/rustdoc/clippy构建。

清理统一负责人是本轮`synth_verify`子代理；交接写作时它正在做全项目inventory、活动/冻结引用闭包和精确删除清单，**此段尚不是清理完成收据**。最终结果会在交付文件前补充。
