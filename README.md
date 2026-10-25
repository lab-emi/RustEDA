# RustEDA

RustEDA 全系列的开发入口，聚合五个产品项目及它们共同依赖的 RustEDA-Common。
各模块保留独立 Git 历史和 Cargo 构建；本仓库固定它们的组合版本，管理 agent 约定与跨仓验证。

## 开始开发

需要 Git、Python 3.11+、Rust 1.97.1，以及原生 Linux 构建环境。开发辅助脚本只使用 Python 标准库，
不会给任何 Rust 核心 crate 添加 Python 依赖。

~~~bash
git clone --recurse-submodules git@github.com:lab-emi/RustEDA.git
cd RustEDA
./dev status
./dev doctor
~~~

RustEDA、RustEDA-Common、RustROAD、RustLane 和 Studio 当前是私有仓库。克隆者需要这些仓库的访问权限，
GitHub 的普通单仓 GITHUB_TOKEN 不会自动获得其他私有仓库的权限。

先阅读 [AGENTS.md](AGENTS.md) 和 [HANDOFF.md](HANDOFF.md)。模块选择与接口边界见
[模块指南](docs/modules.md)，初始版本来源见 [接入记录](docs/bootstrap.md)。

## 统一命令

~~~bash
./dev init                         # 按 gitlink 初始化，不追逐远端最新分支
./dev status --json
./dev doctor                       # 开发环境、固定版本、共享文件完整性
./dev shared                       # 共享源哈希、三个消费者的固定版本和锁文件
./dev test RustEDA-Common           # 在唯一的共享实现上运行测试
./dev fmt all
./dev check RustSys RustSTA
./dev test RustLane
./dev check RustEDA-Studio --no-default-features
./dev build RustROAD --timeout 600
~~~

已有本地仓库时，可用 ./dev init --from-local /path/to/parent 初始化缺失模块。
该目录下必须包含同名仓库及固定提交；不会移动、重置或提交原目录里的文件。
初始化前会检查全部选中模块，任何脏工作区、版本偏移或非空冲突目录都会阻止初始化。

构建与测试串行运行，默认两个构建 job、两个测试线程、8 GiB RAM / 2 GiB swap 的 cgroup
上限及每模块 180 秒超时。缺少用户 cgroup 时使用纯 CPU 地址空间上限。
大于 4 GiB 的运行取得 .work/scale.lock；锁已占用即退出。
目标目录统一为 .work/target/模块名，日志和资源记录写入 .work/logs/。
有 GNU time 时使用 time -v，否则使用标准库 getrusage 记录实际 RSS。
两者报告的进程 RSS 不代表整个进程组的同时驻留总量；cgroup 负责总内存上限。
可以显式调整 --memory-mib、--timeout、--jobs、--test-threads；脚本拒绝无界参数。

./dev doctor 只证明开发入口与共享文件状态，不证明综合、时序或物理签核通过。
EDA 正确性验证仍需要模块规定的独立 oracle、双 PDK、负控制和资源预算，见
[集成验证边界](integration/README.md)。

## 跨仓改动

1. 从本仓库进入任务，确定涉及的模块、接口与验收条件。
2. 在涉及的子仓创建任务分支。多个 agent 使用独立检出目录，不共用可写工作区。
3. 完成模块测试及跨仓验证，先让子仓提交在远端可获取。
4. 在根仓更新对应的 gitlink，将兼容的版本组合放在同一个 PR 中。

~~~bash
git -C modules/RustSTA switch -c feat/example
# 在模块中提交、推送并完成审查
git add modules/RustSTA
git commit -m "integration: update RustSTA for example"
~~~

gitlink 是模块版本的唯一依据；.gitmodules 只保存地址和路径。
根仓没有 Cargo workspace，不会改变各模块的 Cargo.lock、依赖和构建选项。
RustLane 负责产品的运行时流程，本仓库负责开发与集成。

## 当前范围

- 五个模块接入并可从同一目录开发。
- 四个共享库及公共 CLI 实现在 RustEDA-Common，消费者通过同一 Git 提交依赖。
- 各产品保留薄 CLI 入口与本地回归测试；编辑方式见 [共享库工作流](docs/shared-foundation.md)。
- 原目录里的 RustLane 未提交工作没有被自动合并，见接入记录。
- PDK、参考工具、大型缓存与实验数据留在 Git 外，工具模板见 integration/tools.example.toml。
- 根仓 CI 验证开发辅助脚本；不会将其结果称作 EDA 验证通过。

License: Apache-2.0。各子模块继续遵循自己的 LICENSE 与 NOTICE。
