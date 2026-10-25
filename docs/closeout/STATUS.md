# Claude 遗留工作收尾（2026-10-25）

本轮基线 16 个“设计 × PDK”组合均满足既定门禁：**15 项普通通过，1 项 `deviation_declared`，0 项失败，0 项未验证**。
资格验证基于 RustROAD `1f86687baaf7e6753addb9351b83f3561fd714f4` 的干净独立检出目录；全部运行保留负控制、第二次完整构建与制品绑定。
这是块级验证，不是晶圆厂流片签核。下一阶段尚未开始。

| 设计 | SKY130 | GF180 |
| --- | --- | --- |
| pipeline | 通过 | 通过 |
| dense | 通过 | 通过 |
| counter | 通过 | 通过 |
| uart_tx | 通过 | 通过 |
| alu | 通过 | 通过 |
| fifo | 通过（声明偏差） | 通过 |
| regfile | 通过 | 通过 |
| mult | 通过 | 通过 |

## 已收尾

- 审查并验证 GF180 DRV WIP；`set_max_transition 1.0` 保持不变。alu/regfile/mult 完成闭合，两个时序引擎和两种 SPEF 均验收。
- 实现严格 MPW 声明规则：原始差值保留；参考驱动波形阶跃单独定位；另一侧邻近采样原始差值 ≤0.2 ps；独立晶体管/RC 实验必须支持 RustSTA。声明逐项记录并传播到总状态。
- 原先对 mult 的声明未通过电路证据，保持失败后改进实际时钟负载。mult/regfile 的 SKY130 时钟叶扇出从 8 降至 4；原周期、SDC 和验收门限未改。这两个组合最终普通通过。
- dense/GF180 的延迟单元移除后仍满足 hold，说明该修改不是有效坏样本。保留其三个角、双引擎测量，并强制两工具检出另一个显式 2 ns hold-uncertainty 坏样本；真实漏检与检查不完整仍失败。
- 修复当前 Verilator 的公开 JSON 端口接口、工具路径和测量程序定位；固定 Ngspice 与模型目录哈希。保留旧工具身份和失败回执，没有改写旧数据。
- 为 SPEF `whatif insert_buffer` 增加安全边界回归：缺少新拓扑寄生数据时明确拒绝，工作区不变，后续 resize 候选继续评估。文档给出重新布局布线、提取并导入的正确流程。
- 更正旧报告统计：其逐行证据是 **10/16 通过、6 项失败**，并非文字写的 11/16。
- 修正 SERV 许可证归属：`serv_compdec.v` 是 Apache-2.0，其余相应源文件保留 ISC 头；补充许可证与通知。

## 验证与证据

- [完整门禁汇总](../../integration/evidence/claude-closeout.json)：16 行、源版本、摘要哈希、制品绑定、原始 MPW 偏差及声明。
- [模块详细证据](../../modules/RustROAD/docs/evidence/rtl2gds-closeout-2026-10-25.json)：时序、QoR、控制与资源数据。
- [辅助验证](../../integration/evidence/closeout-support.json)：137 项本地 Python 测试、5 项 RustSTA latch CLI 测试、独立 Ngspice RC/双 PDK 极性测试、格式/Clippy/布局/595 项需求覆盖，以及根仓 12 项测试。
- [RustROAD CI](https://github.com/lab-emi/RustROAD/actions/runs/40163108785)：辅助验证通过；CI 结果不冒充完整物理签核。
- 普通端点对照最大差值 0.786409 ps，仍使用 1 ps 门限。完整运行最大单进程峰值 520.2 MiB；整个并行组受 8 GiB RAM、2 GiB swap、7200 秒限制。
- 原始回执和大制品保留在本地 `.work/closeout/qualified-road-final2/.work/runs/`。各基准的全部未执行范围继续明确标记。

## 仍未关闭或不在本轮资格范围内

- OpenC910 独立 checker 的 4.1 ns 历史差异：迁移前删除了原始网表、SDC 与 checker 输出，已向用户询问备份路径。缺少复现数据，未声称修复或通过。
- 完整 GF180 latch 数值时序覆盖、高电阻 Ceff 残差：极性和有界 RC 检查不等于完整精度验证。见 [RustSTA 核查](STA-TRIAGE.md)。
- SERV/rv_core 工业规模、未使用端口与顶层金属天线策略、填充/密度、IR/EM、SI、OCV/CPPR 等继续保留原边界。
- 法务、晶圆厂与独立工程师审查仍需外部完成。见 [审查材料](EXTERNAL-REVIEW.md)。
- RustLane 原有未提交工作未合并，新的联合流程阶段没有启动；本轮未删除旧证据或原始工作区。

[原始报告](original-status-2026-10-25.md)原文已存档；[负责人决定](owner-decisions-2026-10-25.md)同时记录本次 0.2 ps 定义澄清。没有用这次结果倒改历史结论。
