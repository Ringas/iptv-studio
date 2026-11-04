<div align="center">

# 频谱 · IPTV Studio

**在一个频道库里管理多个直播订阅，为电视和播放器提供统一的播放列表。**

[界面预览](#界面预览) · [快速部署](#快速部署) · [播放器地址](#播放器地址) · [下载发行版](https://github.com/Ringas/iptv-studio/releases/latest) · [Docker Hub](https://hub.docker.com/r/talktoo/iptv-studio)

</div>

IPTV Studio（频道工作室）适合部署在 NAS 或家庭服务器上。首次启动时频道库为空，添加自己的 M3U 或分组 TXT 订阅后即可使用。比如两个订阅都收录了 CCTV-1，你可以把它们归到同一个频道，分别试看和检测两条线路，再决定让播放器使用哪一条。

## 界面预览

以下截图使用示例频道、节目单和订阅地址。

**频道库** · 按分组浏览频道。每个色点代表一条线路，绿色表示已启用，灰色表示未启用。

![频道库中的分组、线路状态和首选速率](docs/screenshots/library.webp)

<details>
<summary>频道详情 · 查看节目单、比较同一频道的两条线路</summary>

![频道详情弹窗中的节目单、线路分辨率、速率与启停开关](docs/screenshots/channel-detail.webp)

</details>

<details>
<summary>源管理 · 查看直播订阅和 XMLTV 节目单的更新计划</summary>

![源管理中的订阅地址、启停状态和更新间隔](docs/screenshots/sources.webp)

</details>

## 主要功能

| 用途 | 在工作室里可以做什么 |
| --- | --- |
| 整理频道 | 合并同名频道、保留不同来源的线路，调整分组、名称和台标。更新订阅后仍保留人工设置。 |
| 检查线路 | 在网页中直接试看；检测时验证视频能否解码、直播是否继续推进，并记录速率、分辨率和失败原因。 |
| 匹配节目单 | 导入 XMLTV，为频道关联节目单；支持读取订阅声明的 EPG，以及从支持的传输流中提取 DVB EIT 节目信息。 |
| 管理发布 | 按订阅设置更新间隔，按检测结果筛选线路，也可手动启停。隐藏的频道或线路可在「隐藏管理」中恢复。 |

## 快速部署

1. 下载 [compose.yaml](compose.yaml)，在它所在的目录运行：

   ```bash
   docker compose pull
   docker compose up -d
   ```

2. 打开 `http://<服务器局域网IP>:8788`，在「源管理」添加直播订阅，点击「更新订阅和 EPG」。
3. 在「频道库」打开一个频道，试看或检测线路。新线路默认需要通过检测才会出现在播放器订阅中，也可手动启用。

订阅、频道设置和检测记录保存在 `./data`。升级时沿用这个目录，再执行以上两条命令。局域网默认免密，需要密码时可在「设置与订阅 → 管理台访问」设置。

## 播放器地址

将 `http://<服务器局域网IP>:8788/live.m3u` 填入电视或播放器的远程订阅栏。如果播放器单独要求 EPG 地址，填写同一服务器的 `/guide.xml`。播放设备需要能访问清单中的媒体地址。

| 路径 | 内容 |
| --- | --- |
| `/live.m3u` | 带频道分组、台标和节目单链接的 M3U 播放列表 |
| `/live.txt` | 分组 TXT 列表，同一频道的多条线路用 `#` 分隔 |
| `/live.json` | 频道、线路来源及最近的检测结果 |
| `/guide.xml` | 与播放列表使用相同频道 ID 的 XMLTV 节目单 |

## 本地开发

准备 Python 3.10+、Node.js 22+ 和 FFmpeg，在仓库根目录运行 `python app.py`，然后打开 `http://127.0.0.1:8788`。运行 `python verify.py` 可使用临时数据库和模拟直播源检查主要功能。

[开发交接](DEVELOPMENT-HANDOFF.md) · [线路检测研究与复测方法](research/probe/README.md)
