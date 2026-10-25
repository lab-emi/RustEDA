# 共享基础库迁移验收 — 2026-10-25

共享实现已集中到私有的 [RustEDA-Common](https://github.com/lab-emi/RustEDA-Common)。
RustSys、RustSTA、Studio 使用同一个固定提交；各自保留独立构建和薄 CLI 入口。
原始同级仓库、各子项目原有 main 分支以及 RustLane 的未提交工作保持不变。

## 功能与结构

- 四个基础库的 161 个文件与迁移前逐字节一致；公共 CLI 只增加按调用传入产品版本的接口。
- Common 的完整性清单覆盖 267 个文件；三个消费者的适配器/规范清单各 36 个文件并且一致。
- 旧测试名称全部保留：RustSys/RustSTA 各 401 个，Studio 327 个。
- 三个消费者的布局、共享清单与 Clippy 检查通过。需求覆盖矩阵包含实际 Common 源码引用。
- 真实旧、新 CLI 在 SKY130/GF180 两个固定 TT 库上的输出一致：导入、导出、版本、
  历史、错误处理和二进制表哈希均比较。不同产品版本的负控制被拒绝。
- Studio 成功读取两个 PDK 的新工作区，得到预期的 1 个实例、2 条网络、2 个端口，
  并明确报告 timing: not_analyzed。

| 测试范围 | 通过 | 跳过 |
| --- | ---: | ---: |
| Common | 403 | 10 |
| RustSys | 467 | 6 |
| RustSTA（修正源码定位后完整复验） | 411 | 17 |
| Studio 无界面模式（修正清单检查后完整复验） | 136 | 2 |
| Studio GUI 测试平台 | 63 | 0 |
| 根仓开发工具 | 12 | 0 |

所有执行均有并发、内存和时间上限。含编译的消费者运行中，最大单个子进程 RSS
为 2,057,184 KiB，低于 2 GiB；它不是整个进程组的同时驻留总量。
Common 的[私有 CI](https://github.com/lab-emi/RustEDA-Common/actions/runs/40157777739)通过。
完整记录见 [结构化证据](../integration/evidence/shared-foundation.json)。

## 保留的问题

Studio 的完整依赖许可白名单检查仍拒绝 cbindgen、dwrote 的 MPL-2.0，以及
libfuzzer-sys 的 NCSA。这三个结果在未迁移的 Studio 基线上同样存在。
本次没有放宽许可规则，来源和依赖版本检查通过；许可问题需单独处理。

这次验收不新增 RTL-to-GDS 签核、时序精度、CUDA/Metal 或真实桌面渲染结论。
下一阶段仍是 RustLane 联合流程与私有跨仓 CI。
