# LAZod 自动订阅源

该服务自动完成 ZodAccess 的二阶段订阅更新，将最新 Zod 节点合并进现有 `LAZod.yaml`，并通过受随机令牌保护的 Mihomo 订阅地址发布结果。

服务运行在 Docker 中，仅绑定 LA 主机的 `127.0.0.1:8080`。Cloudflare Tunnel 由主机系统的 `cloudflared`/systemd 管理，不在 Compose 内运行。

## 工作流程

1. 直连私有 ZodAccess URL，取得包含 AnyTLS/VLESS 更新专用节点的引导配置。
2. 临时启动隔离的 Mihomo，优先经 AnyTLS 节点重新请求订阅，失败时回退 VLESS。
3. 删除说明节点和更新专用节点，保留 V0 与正常节点。
4. 保留基础 LAZod 的 LA 节点、DNS、规则及服务组，动态重建 USA/HKG/TWN/JPN/SGP 区域组。
5. 检查所有引用并执行 `mihomo -t`；成功后原子发布，失败则保留上一版本。

更新在容器启动时立即执行，并默认于 UTC 00:00、06:00、12:00、18:00 自动执行。

## 1. LA 主机准备

要求：

- Linux x86_64 或 arm64；
- Docker Engine 与 Compose 插件，或兼容的 Podman Compose；
- systemd；
- 已托管到 Cloudflare 的域名；
- 系统已安装官方 `cloudflared`。

将项目复制到 LA 主机后，在项目目录中准备私密配置：

```bash
cp .env.example .env
./scripts/generate-secrets.sh
```

把脚本输出的两个令牌分别写入 `.env`，同时填写真正的 `ZOD_SUBSCRIPTION_URL`。两个令牌必须不同，不要使用示例值。

现有的 `LAZod.yaml` 直接放在项目根目录。服务只保留 `STATIC_PROXY_NAMES` 指定的自建节点，默认值为 `LA`；其余旧节点会被最新 Zod 节点替换。

限制敏感文件权限：

```bash
chmod 600 .env LAZod.yaml
```

`.dockerignore` 已排除 `.env`、`LAZod.yaml` 和 `ZodAcc.yaml`，这些文件不会进入镜像层。

## 2. 构建与启动更新器

```bash
docker compose build --pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 updater
```

Mihomo 固定为官方 `v1.19.29`，构建时按 amd64/arm64 分别校验官方 SHA-256。容器使用只读根文件系统、丢弃全部 capabilities，并只发布：

```text
127.0.0.1:8080 -> container:8080
```

首次更新尚未成功时 `/healthz` 返回 503；生成首个有效版本后返回 200：

```bash
curl --fail http://127.0.0.1:8080/healthz
ss -ltnp | grep ':8080'
```

`ss` 的监听地址必须是 `127.0.0.1:8080`，不能是 `0.0.0.0:8080` 或 `[::]:8080`。不要在防火墙或云厂商安全组开放 8080。

## 3. 配置系统级 Cloudflare Tunnel

下面使用 locally-managed Tunnel；若已有 Tunnel，可以直接复用其 UUID 和凭据。

```bash
cloudflared tunnel login
cloudflared tunnel create lazod-subscription
cloudflared tunnel route dns lazod-subscription sub.example.com
```

复制示例并替换 Tunnel UUID、凭据路径和域名：

```bash
sudo install -d -m 700 /etc/cloudflared
sudo install -m 600 deploy/cloudflared/config.yml.example /etc/cloudflared/config.yml
sudo install -m 600 "$HOME/.cloudflared/<TUNNEL_UUID>.json" /etc/cloudflared/<TUNNEL_UUID>.json
sudo editor /etc/cloudflared/config.yml
```

目标路由应保持为宿主机回环地址：

```yaml
ingress:
  - hostname: sub.example.com
    service: http://127.0.0.1:8080
  - service: http_status:404
```

验证配置并注册 systemd 服务：

```bash
sudo cloudflared tunnel ingress validate
sudo cloudflared --config /etc/cloudflared/config.yml tunnel run <TUNNEL_UUID>
# 前台验证成功后按 Ctrl-C 退出，再安装服务
sudo cloudflared service install
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared
sudo journalctl -u cloudflared -n 100 --no-pager
```

不要为该主机名配置 Cloudflare “Cache Everything” 规则。应用响应已包含 `Cache-Control: private, no-store`，以避免节点凭据进入边缘缓存。

## 4. 使用接口

最终订阅地址：

```text
https://sub.example.com/sub/<SUBSCRIPTION_TOKEN>/LAZod.yaml
```

令牌错误统一返回 404。配置响应带有 `ETag`、`Last-Modified`、`nosniff` 和禁止缓存头。

手动触发刷新：

```bash
curl --fail-with-body \
  -X POST \
  -H 'Authorization: Bearer <ADMIN_TOKEN>' \
  https://sub.example.com/admin/refresh
```

查看脱敏状态：

```bash
curl --fail-with-body \
  -H 'Authorization: Bearer <ADMIN_TOKEN>' \
  https://sub.example.com/admin/status
```

状态只包含刷新时间、是否运行、Zod 节点数、使用的 `AN`/`VL` 路径、输出哈希和错误代码，不返回 URL、节点名或认证材料。

常见错误代码：

| 错误 | 含义 |
|---|---|
| `missing_update_nodes` | 首次订阅没有可用的 AN/VL 更新节点 |
| `bootstrap_only` / `too_few_nodes` | 二次请求仍是引导态或正常节点不足 |
| `invalid_yaml` / `forbidden_control` | 上游内容损坏 |
| `mihomo_not_ready` | 更新专用节点无法启动或连接 |
| `mihomo_validation_failed` | 生成后的完整配置未通过 Mihomo 校验 |
| `all_update_nodes_failed` | AN 与 VL 均失败；当前有效配置未被替换 |

## 5. 日常运维

更新失败不会删除或覆盖 `/data/current.yaml`。命名卷中还保留最近 5 个成功版本，可通过以下命令确认：

```bash
docker compose exec updater sh -c 'ls -l /data/current.yaml /data/versions'
```

轮换订阅令牌时，修改 `.env` 中的 `SUBSCRIPTION_TOKEN` 并重建容器：

```bash
docker compose up -d --force-recreate updater
```

旧 URL 随即失效。`ADMIN_TOKEN` 可独立轮换，不影响客户端订阅 URL。

更新基础 LA 节点或规则后，修改 `LAZod.yaml` 并触发手动刷新；只有合并结果通过 Mihomo 校验后才会发布。

从另一台机器验证 8080 未暴露：

```bash
nc -vz <LA_PUBLIC_IP> 8080
```

该连接必须失败，而 Cloudflare HTTPS 订阅地址应正常返回。

## 6. 本地测试

推荐使用 `uv` 创建隔离环境：

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

测试覆盖引导解析、AN 到 VL 回退、损坏 YAML、节点过滤、动态分组、悬空引用、原子发布、版本保留、认证、响应安全头和刷新互斥。
