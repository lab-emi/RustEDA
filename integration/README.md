# 集成验证

根仓的结构测试、共享文件检查或 cargo check 成功不构成 EDA 验证。
已运行的验证以范围明确的证据为准：[验证范围与证据](../docs/verification.md)、
[联合流程指南](../docs/flows.md)。其他设计和环境不能直接继承通过结论。

后续验证按变更范围选择：

| 变更 | 必需的证据 |
| --- | --- |
| 共享库 / 文件格式 | 生产者与消费者往返、已存文件兼容性、共享文件校验 |
| 综合 | RustSys 规定的等价与独立黑盒差分；SKY130 / GF180 |
| 时序 | 增量与全量一致性、独立参考、明确 corner / wire model / 未约束端点 |
| 物理流程 | 同一输入与固定工具版本的端到端证据、DRC/LVS/时序范围 |
| RustEDA flow | 子进程失败、取消与恢复；真实子工具协议 |
| RustEDA workbench | 工作区只读模型、选择句柄、版本过期行为和界面检查 |

执行前估算峰值 RAM、限制并发/时间/内存。外部工具缺失标记 unverified；
未运行的检查标记 unchecked，不能填写为零。重型运行必须使用独立的目标和证据目录，
保留原有已固定哈希的工具二进制。

tools.example.toml 是配置模板而非已完成的工具锁定；填写路径后还需实测版本、
二进制哈希和 PDK 文件哈希。开发脚本不会自动下载 PDK、复制参考实现或启动服务。

`./rusteda tools configure` / `verify` 提供当前 RustROAD 固定配置的本机锁定与身份验证。
`verify_phase3_flow.py` 通过根目录 CLI 执行双 PDK、完整生产者门禁、缓存复用和陈旧版本拒绝；
`verify_worker_control.py` 验证实际容器的取消和恢复；`--with-root-flow` 还覆盖根入口、
全局任务登记、flow workspace 修订和显式重试。它们不能作为 EDA 数值 oracle。

`python3 scripts/check_repository_boundaries.py` 检查受版本控制的源码、文档、CI 和已初始化模块，禁止引用已删除仓库的地址或旧检出路径。历史验证记录按固定 SHA-256 保留；内部 `rusteda-studio` crate 和兼容的 `rustlane.*` 格式不属于独立仓库依赖。
