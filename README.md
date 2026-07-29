# M3U8 Downloader

一个可通过 Docker 运行的 HLS/M3U8 下载服务。支持在线 URL 和上传 M3U8 文件、分片级重试、断点续传、内存任务队列、简单管理页，以及最终 MP4 输出。

仅下载你有权保存的媒体。本项目不支持 Widevine、FairPlay 等 DRM，也不包含任何 DRM 绕过逻辑。

## 功能

- URL 或 M3U8 文件提交
- master playlist 自动选择源中的最佳画质，不做缩放
- `User-Agent`、`Referer`、`Cookie`、`Authorization` 请求头
- 可按任务忽略异常 HTTPS 证书链，默认仍严格验证证书
- yt-dlp 原生 HLS 分片下载和 `.part/.ytdl` 续传
- 分片重试和任务级重试
- FFmpeg 优先无损封装，必要时转为 H.264/AAC
- ffprobe 验证最终 MP4
- 任务状态仅保存在内存中，不引用数据库
- 管理页和 API 无需 Token，URL/路径校验并对敏感日志脱敏

第一版仅面向 VOD 点播。直播录制、动态刷新过期 Token、多用户权限和分布式 worker 不在当前范围内。

## 快速启动

要求 Docker Engine 或 Rancher Desktop/Docker Desktop 已启动。

```powershell
Copy-Item .env.example .env
```

按需编辑 `.env` 中的下载目录：

```dotenv
DOWNLOAD_DIR=D:/Videos
```

Windows 路径使用正斜杠，并确保 Docker Desktop 或 Rancher Desktop 有权访问该盘符。然后运行：

```powershell
docker compose up --build -d
docker compose ps
```

在群晖 Container Manager 中使用“项目”部署时，将卷改成群晖上的绝对路径，例如：

```yaml
volumes:
  - /volume1/docker/m3u8-downloader/data:/data
  - /volume1/video/m3u8-downloads:/downloads
```

容器启动时会修正这两个挂载目录的所有者，然后以非 root 用户运行服务，因此通常无需
手工将宿主机目录设置成 UID `10001`。群晖共享文件夹 ACL 仍需允许 Container Manager
访问；不要在项目配置中设置 `user`，也不要将这两个卷挂载为只读。

打开：

- 管理页：http://localhost:8080
- Swagger：http://localhost:8080/docs
- 健康检查：http://localhost:8080/healthz
- 就绪检查：http://localhost:8080/readyz

管理页无需 Token，直接填写 M3U8 地址或上传文件即可使用。

如果可信来源报 `CERTIFICATE_VERIFY_FAILED`，可勾选“忽略 HTTPS 证书错误”后重新提交任务。该选项会关闭该任务对 playlist、分片和密钥请求的服务器身份验证，只应对你确认可信的来源使用。

## API 示例

提交在线 M3U8：

```powershell
$headers = @{ "Content-Type" = "application/json" }
$body = @{
	url = "https://example.com/master.m3u8"
	output_name = "episode-01"
	output_subdir = "shows/season-1"
	headers = @{ Referer = "https://example.com/" }
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/tasks/url -Headers $headers -Body $body
```

查询任务：

```powershell
Invoke-RestMethod http://localhost:8080/api/v1/tasks
```

上传的 M3U8 如果包含相对的分片、密钥或变体 URI，必须提供 `base_url`。服务会结构化解析并转换成 HTTP/HTTPS 绝对 URI；不接受 `file:`、`data:` 和其他本地协议。

## 存储

容器内目录：

- `/data/work/{task_id}`：断点文件和临时媒体
- `/data/logs/{task_id}.log`：脱敏任务日志
- `/downloads/{subdir}/{name}-{task_id前8位}.mp4`：最终视频

任务元数据仅存在于 API 进程内存中，服务或容器重启后任务列表会清空。宿主机的 `./data` 保存临时断点文件和日志，`${DOWNLOAD_DIR}` 保存最终 MP4；重启后不会自动恢复旧任务。

## 输出画质

服务始终下载源 playlist 中的最佳可用流，不提供画质选择，也不会在 FFmpeg 阶段添加缩放滤镜。源视频是 1920x1080 时，封装或必要的 H.264/AAC 转码仍保持 1920x1080；服务不会把低分辨率源放大成 1080p。

## 重试和恢复

- 每个分片默认重试 10 次，使用指数退避。
- 整个任务默认尝试 3 次。
- 永久缺失的分片不会被跳过，任务会失败，避免静默生成残缺视频。
- 取消会通知当前下载线程停止，并保留工作目录。
- 对已取消或失败任务执行“重试”会复用已有 `.part/.ytdl` 数据。
- 删除任务会删除工作目录和日志；最终 MP4 默认保留。

## 本地开发

本机若没有 FFmpeg，可以运行 API 单元测试，但 `/readyz` 会返回 503。完整下载请使用 Docker 镜像。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

本机运行 API：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8080
```

必须只使用一个 Uvicorn worker。任务 supervisor 位于 API 进程中，多 worker 会导致重复领取任务。

## 安全边界

当前版本按本机或可信内网部署设计：

- 仅允许 HTTP/HTTPS 来源。
- 拒绝初始 URL 指向 loopback、私网、link-local 和保留地址。
- 输出路径只能是 `/downloads` 下的相对目录。
- 请求头和来源 URL 仅保存在进程内存中，Cookie、Authorization 和 URL query 在日志中脱敏。
- 容器以非 root 用户运行，删除 Linux capabilities，并使用只读根文件系统。

不要直接暴露到公网。公网版本还需要反向代理 TLS、用户级认证、限流，以及独立下载 worker/出口代理来强制阻断嵌套 playlist 和 DNS rebinding 对内网的访问。