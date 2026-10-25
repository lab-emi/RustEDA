# 共享库开发工作流

唯一实现来源是 modules/RustEDA-Common。产品目录中的四个核心库已移除，公共
rusteda-cli 保留薄入口；请直接在共享库中修复实现，避免重新复制代码。

## 日常操作

~~~sh
./dev status
./dev test RustEDA-Common
./dev test RustSys
./dev test RustSTA
./dev test RustEDA-Studio --no-default-features
./dev shared
~~~

共享变更的顺序：

1. 在 Common 的任务分支修改、验证并提交，推送到授权可访问的远端分支。
2. 将三个消费者 Cargo.toml 中四个基础库和 rusteda-cli-shared 的 rev 一并更新为
   该完整提交号，随后在各消费者运行 cargo update --workspace。
3. 在消费者运行所需回归。公共 CLI 适配器必须继续传入产品自己的版本。
4. 更新 Common 和消费者的根仓 gitlink；运行 ./dev shared 检查版本闭合，再提交根仓。

仅修改 Common 的工作区不会改变其他产品当前使用的已固定提交；这有助于区分正在开发的
版本和已验收的版本。不要用浮动分支代替 rev，也不要将 Cargo.lock 的本地路径覆盖提交。

消费者的 cargo xtask coverage 会定位真实共享依赖源码并记录 @common/ 引用。
维护共享库清单使用 Common 内的 python3 scripts/shared_manifest.py；
消费者仍用 cargo xtask shared update 维护其薄适配器和公共规范清单。

## 验证边界

Common 的 CPU 测试不代表 CUDA、Metal 或签核全部通过。产品级算法改动仍遵守
各自的独立验证、双 PDK 与资源限制。迁移兼容性工具为 integration/check_cli_compat.py，
它比较两个实际可执行文件，并且只归一化时间戳、工作区路径和可执行文件路径；
版本、诊断、导出结果和二进制表仍必须一致。
