# 模块与任务入口

| 模块 | 目录 | 负责的工作 |
| --- | --- | --- |
| RustSys | modules/RustSys | 综合、映射、等价检查 |
| RustSTA | modules/RustSTA | 时序、约束、寄生参数与独立 checker |
| RustROAD | modules/RustROAD | 物理设计、布局布线、提取、GDS |
| RustLane | modules/RustLane | 持久化流程编排与外部进程管理 |
| RustEDA-Studio | modules/RustEDA-Studio | 工作区可视化与人机交互 |
| RustEDA-Common | modules/RustEDA-Common | 共享模型、格式、磁盘工作区、计算内核和公共 CLI |

开始任务先读根 AGENTS.md / HANDOFF.md，再读模块自己的指导文件。若模块的 HANDOFF 没有
纳入 Git，使用 docs/handoffs/ 中保存的初始历史记录；这些记录中的相对链接按原仓库解释。
历史记录里的机器绝对路径和旧版本描述不应直接当成当前配置。

## 接口边界

综合、时序与物理流程通过明确版本的文件或 CLI 协议交换输入输出；不要为了联合构建
就把所有模型合并成一个 IR。模块各自维护 Cargo workspace 与 Cargo.lock。
根目录没有 Cargo.toml，使用 ./dev 选择模块执行。

共享实现集中在 RustEDA-Common，包括 rusteda-core、rusteda-workspace、
rusteda-compute、rusteda-formats 和 rusteda-cli-common。产品内的 rusteda-cli
只负责调用共享实现并保留自己的版本信息。变更后须检查消费者兼容性、版本与锁文件，
不能只更新生产者。

## 并行开发

任务分支按模块创建，同一任务记录所有基准 SHA 与关联 PR。一个 agent 一个检出目录；
不要在另一个 agent 正在验证的目录里改文件。根仓的额外 worktree 不会自动初始化
子模块；需要独立的子模块工作目录，或使用单独的递归克隆。

原始同级目录不是新聚合仓的共享可写后端。子模块是独立克隆，不是符号链接；
对 modules/ 的正常编辑不会改动 ~/git/RustSys 等原始工作区。
