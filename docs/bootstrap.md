# 初始接入记录 — 2026-10-25

本地入口：~/git/RustEDA。GitHub：lab-emi/RustEDA，私有。

| 模块 | 初始固定提交 | 来源 |
| --- | --- | --- |
| RustSys | d36d922c0201968745be72bd71aff57ace393c90 | 原 feat/shared-convergence |
| RustSTA | 455c43c9bd6afa0e0a538822d3fc2822d6e26880 | 原 feat/signoff-checks |
| RustROAD | 9e85069d6a39b22fa54450d221e543a80bd1faba | 原 feat/rtl2gds |
| RustLane | 6c4824aabc7d03fec82d550fa34506c4be430513 | 已提交基线，未包含本地 postroute WIP |
| RustEDA-Studio | 86d4e10fd6c93789fccf555bdcd6f222cce0b66f | 271 个已有文件的独立初始快照 |

此表是历史接入记录；后续实际固定版本以 Git gitlink 为准。
前三个模块的已有本地提交通过 integration/rusteda-bootstrap-20261025 分支提供，
原有 main 保持原状。RustLane 已提交基线本来就可从远端获取。
Studio 在新的私有仓库保存快照；源目录没有被初始化提交或改写。

## 被保留的本地工作

- 原 RustSys、RustSTA、RustROAD 目录与分支保持原状。
- 原 RustLane 的未提交修改与新文件仍在 ~/git/RustLane，包括 post-route 流程；
  需要单独审查、提交、验证后再更新此处的 gitlink。
- 原 RustEDA-Studio 仍是无首个提交的源目录，便于核对。后续开发应转到
  modules/RustEDA-Studio，避免同时编辑两个副本。
- 所有 .work、target、PDK 与参考工具缓存都没有被复制进仓库。

## 已运行的检查

- 9 个真实临时 Git 仓库测试：版本固定、脏工作区保护、提交保护、全量预检、
  本地初始化、路径越界/符号链接拒绝、共享哈希失败检测。
- 首轮测试 1.66 秒，最大子进程 RSS 23,180 KiB；512 MiB 地址空间限制，
  90 秒超时。RSS 用系统 getrusage 实测，本机未安装 GNU time。
- 五个模块的 cargo fmt --all -- --check 均通过；每模块使用 512 MiB cgroup，
  120 秒超时，资源记录在本地 .work/logs。
- RustSys 与 RustSTA 各 209 个共享文件内容通过清单哈希核验且集合一致。
- Studio 的 181 个共享文件通过自身清单核验，但与统一参考有 88 个不同/缺少路径；
  明确保留为第二阶段任务。
- 开发入口与 Rust 1.97.1 可用。未运行全系列编译、EDA 回归、双 PDK 签核或 GUI 功能验证。

本地原始输出位于 .work/bootstrap 和 .work/logs，未提交大型或机器相关产物。
