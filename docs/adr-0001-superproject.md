# ADR 0001 — 用 Git superproject 建立统一开发入口

Status: accepted for the initial integration phase, 2026-10-25.

五个项目已有独立历史、Cargo workspace、验证流程与未完成工作。第一阶段通过 Git submodule
固定版本组合，统一开发约定、模块选择和集成证据，同时保留各项目的开发边界。

根仓不建立总 Cargo workspace；不引入新守护进程；不移动现有工作目录；
不把 PDK、参考工具源码或实验缓存作为子模块纳入。共享 crate 暂时保留当前同步机制。
后续单一共享来源的迁移需要单独审查 API、工作区文件格式和各消费者验证结果。

代价是跨仓变更涉及多个提交。先保证子仓提交可获取，再提交根仓的 gitlink 更新；
根仓 PR 表达一组兼容版本。如果大多数任务持续跨仓修改，共享代码迁移或 monorepo
应成为下一项组织决策，而不是继续增加自动复制代码的机制。
