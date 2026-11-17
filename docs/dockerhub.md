# 频谱 · IPTV Studio

自托管 IPTV 频道与多线路管理工具：导入自己的直播订阅，检测并整理线路，为电视和播放器输出统一订阅。首次启动为空频道库，不内置直播源。

**镜像：** `talktoo/iptv-studio:{{VERSION}}` · `latest` 跟随最新稳定版  
**平台：** `linux/amd64`，适用于 x86-64 NAS、Linux 服务器及使用 Linux 容器的 Docker 环境。

## Docker Compose 部署

将以下内容保存为 `compose.yaml`：

```yaml
{{COMPOSE}}
```

在同一目录启动：

```bash
docker compose pull
docker compose up -d
```

打开 **`http://服务器局域网IP:8787`**，在「源管理」添加 M3U / TXT 直播订阅；需要节目单时添加 XMLTV。随后检测线路、调整启停，并将订阅地址填入播放器。

## 数据与端口

| 配置 | 说明 |
| --- | --- |
| `8787:8787` | 左侧为宿主机端口，右侧为容器端口。宿主机端口冲突时可改为 `8788:8787` |
| `./data:/app/data` | 保存频道、订阅、设置、检测记录和管理密码。升级时继续挂载原目录 |
| `init: true` | 回收检测、解码等子进程 |

默认使用上海时区，**无需在 Compose 中设置 `TZ`**。需要其他时区时，才在 `environment` 中添加，例如 `TZ: Europe/London`。

应用以 **UID/GID 1000:1000** 运行。首次挂载空目录时会设置所需权限；已有目录应允许该用户读写。不要同时启动多个实例访问同一数据库。

## 播放器订阅

将服务器地址替换为你的实际地址：

| 地址 | 用途 |
| --- | --- |
| `http://服务器IP:8787/live.m3u` | M3U 频道与线路 |
| `http://服务器IP:8787/live.txt` | 分组 TXT 多线路 |
| `http://服务器IP:8787/live.json` | 结构化频道数据 |
| `http://服务器IP:8787/guide.xml` | XMLTV 节目单 |

导出的媒体地址通常由播放器直连上游，请确保播放器也能访问直播源。网页试播支持 HLS、MP4、WebM，实际解码能力取决于浏览器。

## 更新与回退

升级前停止服务并备份整个数据目录，保留原 Compose 和数据挂载：

```bash
docker compose stop
# 在此时备份 ./data，再继续升级
docker compose pull
docker compose up -d
```

需要固定版本时，把镜像改为 `talktoo/iptv-studio:{{VERSION}}`。旧版本标签会保留，例如 `v1.1.0`；回退前同样备份数据，必要时恢复对应版本的数据备份。

## 日志与排错

```bash
docker compose logs --tail=100 -f iptv-studio
```

默认按“时间 · 类型 · 状态 · 内容”输出简短中文日志：处理进度蓝色、数据结果绿色、策略说明和耗时汇总黄色；整行按内容类型着色，不按成功或失败变色。时间形如 `2026-11-17 16:30:25.123`。后台轮询和逐片信息默认隐藏。持续遇到问题时，在 `environment` 中按需添加：

```yaml
      IPTV_LOG_LEVEL: DEBUG
      IPTV_LOG_FORMAT: text
```

保存后执行 `docker compose up -d` 应用配置。需要结构化诊断时，将 `IPTV_LOG_FORMAT` 改为 `json`；排查完成后恢复 `IPTV_LOG_LEVEL: INFO`。终端及容器默认自动按内容类型着色；若查看器不支持颜色，可设 `IPTV_LOG_COLOR: never`，需要强制启用则设 `always`。JSON 日志始终不包含颜色控制字符。

- **打不开管理台：** 检查容器是否运行、端口映射和 NAS 防火墙。
- **数据目录不可写：** 检查挂载目录是否允许 UID/GID 1000:1000 读写。
- **更新失败：** 查看订阅地址、网络与授权；失败时保留旧快照。
- **429、超时或网络拦截：** 表示本次未能验证，可稍后复核，不直接认定废源。
- **网页无法播放：** 查看同一时间的播放提示，也可切换线路或使用独立播放器复核。

可在服务下配置 Docker 日志轮转，限制宿主机日志占用：

```yaml
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

## 访问设置

默认使用可信局域网访问。可在「设置与订阅 → 管理台访问」设置密码，登录账号为 `admin`；也可用环境变量 `ADMIN_PASSWORD` 指定密码。

公网部署需配置 HTTPS 反向代理、管理员密码及 `ALLOW_PUBLIC_ACCESS: "1"`；使用 `PUBLIC_BASE_URL` 指定对外地址。请勿把家庭订阅或凭证写入公开说明。

## 项目与许可

[项目与完整使用说明](https://github.com/Ringas/iptv-studio) · [版本更新与下载](https://github.com/Ringas/iptv-studio/releases) · [日志排错说明](https://github.com/Ringas/iptv-studio/blob/main/docs/logging.md)

项目采用 **GPL-3.0-only**。镜像保留项目、HLS.js、Node、FFmpeg 及基础系统的必要许可文件。
