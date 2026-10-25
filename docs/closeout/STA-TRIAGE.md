# RustSTA 遗留项核查

本轮没有修改时序引擎或共享数值实现。SPEF 安全边界增加了回归测试与说明；
不能用这些测试把未解决的数值问题标成通过。

| 原报告项目 | 核查结论 | 当前证据与后续边界 |
| --- | --- | --- |
| `whatif insert_buffer` 遇到 SPEF 被拒绝 | 当前接口没有新网的布局布线和寄生数据，拒绝是必要保护 | 新测试检查 `T0070`、候选无数值、磁盘文件不变，后续 resize 候选仍能评估；完整五项 latch CLI 测试通过 |
| GF180 latch 与参考差异 | 已有 `T0150` 和 STA-REQ-0230 解释互补内部状态的极性差别 | 新增双 PDK 晶体管实验确认透明态 D→Q 正极性；不据此声明完整借时、setup/hold 数值一致 |
| 高电阻 Ceff 残差 | 原模型仍是明示的有限波形近似，未宣称任意大电阻都满足 1 ps 对照 | 独立 Ngspice 驱动和电阻环测试本轮通过；这不能消除原报告的 >1 kΩ 数值残差，仍保持未解决 |
| OpenC910 独立 checker 最大 4.1 ns 差异 | 旧记录仍然需要最小复现和独立修复 | 保留的 `check2.sh` 给出了去掉 false-path 的测试流程；对应原始网表、SDC、checker 输出已在机器迁移时删除。本轮未把 full-rerun 或小电路通过当成大型独立检查器通过 |

## 可复现的检查

- RustSTA：`cargo test -p ruststa-cli --test signoff_latch`。
- 极性电路实验：在根仓库运行
  `python3 scripts/closeout/latch_polarity.py --out NEW_DIRECTORY`，外层使用
  1 GiB 内存与 120 秒时限。结果包括 SPICE deck、工具/模型哈希与实测电压。
- 本轮 RC 验证重新执行了 `ngspice_forward_sensitivity_and_lumped_regime` 和
  `ngspice_resistor_loops_match_nodal_solution`，均通过；原始回执留在
  `.work/closeout/rc-driver` 和 `.work/closeout/rc-mesh`。

历史 OpenC910 摘要有约 150–437 万端点，进程峰值约 13–23 GiB。
直接套用小设计预算不安全。下一次处理 checker 时，应先从有许可且固定版本的输入
恢复确切失败案例，按对象/弧/tag 数重新估算资源，然后缩小为有界复现。
独立 checker 的 `undecided` 或 `disagree` 必须继续保留；不能仅因引擎重算一致而放行。

原始历史说明在 `../handoffs/RustSTA.md`，未由本轮新结果覆盖。
