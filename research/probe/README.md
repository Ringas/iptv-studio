# 线路检测研究 · 2026-11-03 至 11-04

[第一轮报告](REPORT.txt)记录 403 冷却与独立解码复核。[第二轮报告](RESEARCH-2.txt)记录传输栈对照、HLS 刷新节奏和复测。[测量数据](measurements.json)按来源与频道标识对齐两轮五条样本，另存 12 组逐分片 HTTP 对照。已清除私有 URL、节点、请求头及原始错误详情；报告内提到的原始 JSON 文件名对应测量文件中的同名分组。

**建议下一轮优先解决的事项**：让播放与检测共享同一上游请求预算；按入口、清单、分片、解码阶段记录故障；识别已知 JSON 上游超时并保留原因；将“可播放”和“达标”分开显示。扩大样本，引入已知坏源、分时段交替新旧顺序、独立播放器复核。以上是待验证方案。

**复跑**：云环境可直接运行根目录的 verify_playback.py。在能访问原直播源的设备上，将私有 samples.json 放在被 Git 忽略的 work/probe-comparison/ 下，使用已保存的同批样本、请求选项、原设置及历史。脚本将结果写到该目录；请使用独立副本保留既有实测记录。Python、Node、FFmpeg 须在 PATH，FFMPEG_BIN 可覆盖解码器。以下命令均从仓库根目录运行。

- 当期代码：`python research/probe/run.py cadence-pipeline`。
- 第一轮修正版：在提交 `854dab3` 的独立检出副本中运行同一脚本，版本名传 revised-pipeline；也可将该版运行文件复制至独立的工作目录。
- 原始基线：将当前版本的运行文件复制到 work/probe-comparison/baseline/，在该副本内对 playback.mjs 和 probe.mjs 应用 restore-baseline.patch（git apply 或 patch -p1），再运行 `python research/probe/run.py baseline-pipeline`。补丁是从本次代码生成的，勿应用到主源码。
- 独立最高码率多帧解码：`python research/probe/independent.py`。同一媒体地址对照：`python research/probe/transport.py`。运行会请求真实入口，分别需要原网络与私有样本。

这组样本是短时隔离复现。独立解码与各轮探测并非同时进行，HTTP 200 本身亦不证明可播放。原始快照、旧工作区和未脱敏材料由本机保留。
