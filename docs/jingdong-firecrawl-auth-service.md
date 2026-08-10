# 京东云 Firecrawl 鉴权服务：现状与解决方案

> 文档状态：当前部署基线与已采用方案
>
> 最后实时复核：2026-08-09 15:26 CST
>
> 说明：服务状态、端口、资源和版本属于动态事实，实施或验收前必须重新检查。

## 1. 项目目标

在京东云上建立并持续运行一套自托管 Firecrawl 服务，使获授权的用户或自动化 Agent 能够执行网页抓取、搜索、站点映射和爬取任务，同时满足以下要求：

- Firecrawl API 不能以无鉴权方式直接提供给使用者；
- 无凭证或凭证错误的请求必须被拒绝；
- 正确凭证可以通过统一入口访问 Firecrawl；
- Firecrawl、数据库、队列、浏览器和搜索服务只暴露必要的最小网络面；
- API Key 不进入仓库、文档、命令参数、Nginx 日志或普通运维输出；
- 当前阶段不修改 Cloudflare、安全组、防火墙，也不开放公网 `80/443`；
- 服务在京东云约 2 vCPU、4 GB 内存的资源范围内稳定运行。

当前阶段的目标是“具备 Bearer API Key 鉴权的私有 Firecrawl 服务”。它不自动等同于完整的多用户身份、权限、配额和租户隔离系统。

## 2. 问题与约束

Firecrawl 当前采用自托管模式，`USE_DB_AUTHENTICATION=false`。如果使用者通过隧道直接连接 Firecrawl 的 `3002` 端口，Firecrawl 自身不会完成本项目所需的用户鉴权。

直接启用 Firecrawl 数据库认证会扩大实施范围，涉及身份数据、数据库配置和应用行为变化。当前服务器资源有限，同时现阶段没有开放公网入口的需求，因此需要一个变更面较小、可回滚、不会改动 Firecrawl 核心代码的鉴权层。

主要约束如下：

- 京东云 Firecrawl 部署目录为 `/opt/firecrawl`；
- Firecrawl 必须继续绑定云主机回环地址；
- 本机已有 SSH 隧道和 Firecrawl CLI 使用习惯；
- 不使用或回退到 Firecrawl 官方云 API；
- API Key 不能出现在进程参数、Git、日志或交付文档中；
- 当前一次只运行一个真实抓取、搜索、映射或爬取任务，避免压垮单机资源。

## 3. 当前运行现状

### 3.1 主机与服务

京东云主机 SSH 别名为 `jingdong-vps`，主机名为 `lavm-o1g8exgft8`。本次复核时主机已连续运行约 12 天，系统负载较低。

| 组件 | 当前状态 | 作用 |
| --- | --- | --- |
| Nginx `1.24.0` | `active + enabled`，配置检查通过 | Bearer 鉴权和反向代理 |
| Firecrawl API | 运行中，当前未发生 OOM | API 与任务 Worker |
| Playwright service | 运行中 | 浏览器页面抓取 |
| Nuq PostgreSQL | 运行中，`healthy` | Firecrawl 数据存储 |
| Redis | 运行中，`healthy` | 缓存和任务协作 |
| RabbitMQ | 运行中，`healthy` | 消息队列 |
| SearXNG | 运行中，`healthy` | Firecrawl 搜索入口 |

Firecrawl API 当前历史重启计数为 3，`OOMKilled=false`。该计数本身不证明存在当前故障；如需解释重启原因，应继续结合事件时间和历史日志调查。

### 3.2 网络与监听

京东云仅发现以下相关监听：

| 地址 | 服务 | 说明 |
| --- | --- | --- |
| `127.0.0.1:3002` | Firecrawl | 仅供云主机内部访问 |
| `127.0.0.1:3003` | Nginx | 带 Bearer 鉴权的内部入口 |

未发现 `80/443` 监听。Firecrawl 和 Nginx 均未直接暴露到公网接口。

### 3.3 本机接入

macOS LaunchAgent `com.nikcel.firecrawl-jingdong-tunnel` 维护以下 SSH 隧道：

```text
本机 127.0.0.1:3002
  -> SSH: jingdong-vps
  -> 京东云 127.0.0.1:3003 Nginx
  -> 京东云 127.0.0.1:3002 Firecrawl
```

LaunchAgent 文件位于：

```text
/Users/nikcel/Library/LaunchAgents/com.nikcel.firecrawl-jingdong-tunnel.plist
```

本次复核时 LaunchAgent 为 `running`，本机 `127.0.0.1:3002` 由 SSH 进程监听，最近一次退出码为 `0`。

## 4. 已采用的解决方案

### 4.1 方案概览

在 Firecrawl 前增加只监听回环地址的 Nginx 鉴权网关，由 Nginx 校验请求中的 Bearer API Key。只有鉴权成功的请求才会转发给 Firecrawl。

```text
Firecrawl CLI / API Client
        |
        | Authorization: Bearer <secret>
        v
本机 SSH 隧道 127.0.0.1:3002
        |
        v
京东云 Nginx 127.0.0.1:3003
        |-- 无 Key 或错误 Key --> HTTP 401
        |
        |-- 正确 Key
        v
京东云 Firecrawl 127.0.0.1:3002
        |
        +--> Playwright / PostgreSQL / Redis / RabbitMQ / SearXNG
```

### 4.2 鉴权层

- Nginx 作为唯一面向 SSH 隧道的 Firecrawl 入口；
- 无 `Authorization` Header 或 Bearer Key 不匹配时返回 HTTP `401`；
- Key 匹配后，Nginx 将请求反向代理到 `127.0.0.1:3002`；
- Nginx 仅监听 `127.0.0.1:3003`，不监听公网地址；
- 默认 Nginx 站点不参与该服务，当前只启用内部 Firecrawl 站点；
- Firecrawl 仍保持 `USE_DB_AUTHENTICATION=false`，鉴权责任由 Nginx 入口承担。

### 4.3 凭证管理

- 真实 API Key 不写入本仓库或本文档；
- 京东云上的 Key 文件权限为 `root:root 600`；
- 本机 Firecrawl CLI 从权限受限的凭证文件读取自托管 API URL 与 Key；
- CLI 命令必须显式指定 `http://127.0.0.1:3002`，防止回退到官方云；
- 鉴权健康检查通过 curl 配置标准输入传递 Header，Key 不出现在进程参数中；
- Nginx 和容器日志应持续检查是否出现 `Authorization`、`Bearer` 或 Key 特征。

安全健康检查脚本：

```bash
bash /Users/nikcel/.agents/skills/firecrawl/scripts/health-check.sh
```

### 4.4 为什么选择该方案

| 评估项 | Nginx Bearer 网关的效果 |
| --- | --- |
| 对 Firecrawl 核心代码的影响 | 无需修改 |
| 对数据库认证的依赖 | 无需启用 |
| 网络暴露面 | Nginx 与 Firecrawl 都保持回环监听 |
| 与现有 SSH 隧道兼容性 | 只需把隧道远端目标从 Firecrawl 改为 Nginx |
| 凭证保护 | 可使用权限受限文件，不进入仓库和日志 |
| 回滚复杂度 | 配置与 LaunchAgent 均可独立备份和恢复 |
| 当前局限 | 共享 Key 不能提供用户级身份、配额和审计 |

该方案适合当前“私有入口 + API Key 鉴权”的阶段目标，但不是长期多租户控制面的终点。

## 5. 当前验收结果

2026-08-09 的实时检查与业务冒烟测试得到以下结果：

| 验收项 | 结果 |
| --- | --- |
| Nginx 服务 | `active + enabled` |
| Nginx 配置 | `nginx -t` 成功 |
| Nginx 监听 | 仅 `127.0.0.1:3003` |
| Firecrawl 监听 | 仅 `127.0.0.1:3002` |
| 公网 `80/443` | 未监听 |
| 无 Key 请求 | HTTP `401` |
| 错误 Key 请求 | HTTP `401` |
| 正确 Key 请求 | HTTP `200` |
| 鉴权健康响应 | 返回 Firecrawl API 健康 JSON |
| example.com 真实抓取 | 成功 |
| OpenAI 真实搜索 | `success: true`，返回 3 条结果 |
| 核心容器 | 全部运行；具备健康检查的依赖为 `healthy` |
| API 数据库认证 | `USE_DB_AUTHENTICATION=false` |
| OOM | 当前未发现 |
| Key 日志泄露检查 | 未发现匹配模式 |

这证明当前已完成“通过私有 SSH 隧道访问、由 Nginx Bearer Key 鉴权、能够执行真实 Firecrawl 业务”的阶段目标。

## 6. 日常验证方法

### 6.1 本机鉴权健康检查

```bash
bash /Users/nikcel/.agents/skills/firecrawl/scripts/health-check.sh
```

期望返回 Firecrawl API 健康 JSON。脚本失败时，不能立即判断是京东云服务故障，应继续区分本机隧道和远端服务。

该脚本只用于当前“Nginx 固定共享 Key”基线。LocalAuthProvider 切换后不能继续用公开根路由 `/` 证明用户 Key 有效，因为应用根路由本身不执行本地认证；届时必须改用方案中定义的 internal liveness/readiness 和经过 LocalAuthProvider 的 authenticated key probe，并同步更新本运行手册。

### 6.2 检查 LaunchAgent

```bash
launchctl print "gui/$(id -u)/com.nikcel.firecrawl-jingdong-tunnel"
lsof -nP -iTCP:3002 -sTCP:LISTEN
```

期望看到 LaunchAgent 为 `running`，并且 SSH 进程监听本机 `127.0.0.1:3002`。

### 6.3 检查京东云服务

```bash
ssh jingdong-vps 'systemctl is-active nginx && systemctl is-enabled nginx'
ssh jingdong-vps 'nginx -t'
ssh jingdong-vps 'cd /opt/firecrawl && docker compose ps'
```

### 6.4 验证无 Key 请求

```bash
curl --silent --output /dev/null \
  --write-out '%{http_code}\n' \
  http://127.0.0.1:3002/
```

期望返回 `401`。正确 Key 验证只使用安全健康检查脚本，不把真实 Key 写进命令示例。

### 6.5 真实业务验证

一次只运行一个任务：

```bash
firecrawl --api-url http://127.0.0.1:3002 \
  scrape https://example.com \
  -o /tmp/firecrawl-example.md

firecrawl --api-url http://127.0.0.1:3002 \
  search OpenAI --limit 3 --json \
  -o /tmp/firecrawl-search-openai.json
```

容器为 `Up`、健康接口返回 `200`，都不能替代真实抓取和真实搜索验证。

## 7. 故障定位边界

| 现象 | 优先检查 |
| --- | --- |
| 本机端口无监听 | LaunchAgent 状态、SSH 网络、Clash/TUN 路由 |
| 本机端口监听但请求失败 | SSH 通道、远端 Nginx 监听和错误日志 |
| 无 Key 未返回 401 | Nginx 站点、监听目标和鉴权配置 |
| 正确 Key 返回 401 | 本机与服务器凭证是否一致、Key 是否已轮换 |
| 健康接口成功但抓取失败 | API、Playwright、DNS、代理和目标站点策略 |
| 搜索为空或超时 | Firecrawl 搜索日志、SearXNG 与上游搜索引擎 |
| 容器反复重启 | Docker 事件、退出码、OOM、主机资源和依赖日志 |

检查时必须分别报告“本机接入层、SSH 隧道、Nginx 鉴权层、Firecrawl 应用层和真实业务层”的状态，不能用其中一层的成功代替整条链路验收。

## 8. 安全与变更规则

- 不输出、复制或提交任何真实凭证；
- 不使用 `firecrawl login --browser`，不连接 `api.firecrawl.dev`；
- 不把 SSH 隧道改回直连远端 Firecrawl `3002`，否则会绕过 Nginx 鉴权；
- 未经明确授权，不新增公网监听或修改 Cloudflare、安全组、防火墙；
- Nginx 配置、LaunchAgent 和本机 CLI 凭证变更前应创建权限安全的备份；
- Key 轮换应采用原子更新，依次验证错误旧 Key 为 `401`、新 Key 为 `200`；
- 日志检查只报告是否发现敏感模式，不打印匹配到的秘密内容；
- 生产变更完成后必须复验监听、鉴权、健康接口、真实抓取和真实搜索。

## 9. 回滚边界

当前部署保留了 Nginx、LaunchAgent、CLI 凭证和 Skill 的历史备份。需要回滚时，应先解析明确的备份文件和目标版本，再逐项恢复并重新验收。

回滚不能以取消鉴权为默认结果。若必须暂时绕过 Nginx，只能在明确授权、保持回环隔离并限定维护窗口的情况下执行；完成维护后必须恢复鉴权入口。

## 10. 当前未完成能力

以下内容不属于现阶段已完成事实：

- 每个用户或服务账号使用独立 Key；
- 用户级 Key 签发、撤销、轮换和到期管理；
- 用户级限流、并发、队列和资源配额；
- 用户级任务、结果和日志访问隔离；
- 用户级审计和用量统计；
- 高可用、水平扩展和自动故障转移；
- 公网 HTTPS 服务入口。

### 10.1 下一阶段已选技术路线

根据 `authprovider` 分支所包含的最新 Firecrawl `v2.11.193` 源码审计，下一阶段不再在 Nginx 多 Key、直接开启托管数据库认证和外部 API Gateway 三者之间待选，而是采用以下路线：

- 在 Firecrawl 内增加可插拔的 `LocalAuthProvider`，把本地 API Key 解析为原生 `user_id/team_id/org_id/api_key_id`；
- 增加本地注册、会话、Key 创建/轮换/撤销和审计控制面；
- 复用 Firecrawl 已有的 `team_id`、队列 owner、Controller 和 Worker 任务归属能力；
- 同时增加本地策略、用量账本和资源准入层，强制团队/Key 速率、额度、并发和全局容量；
- 将部署形态、认证提供者、任务持久化和团队限制从 `USE_DB_AUTHENTICATION` 中拆开；
- Nginx 继续负责回环入口、Host/路径/方法白名单和反向代理，在切换时由固定共享 Key 校验改为把用户 Bearer Key 传给 Firecrawl；
- 当前阶段不要求独立创建 Gateway 服务；只有未来出现多产品统一入口、跨集群路由或统一商业计费需求时再评估。

源码已有团队级隔离骨架，但并非所有路由都已安全可用。v2 Extract 状态的 owner 检查、async Scrape 状态、本地配额 fail-closed、幂等键租户化等问题必须在开放相关路由前修复并通过 A/B 两租户测试。

完整设计、开发工作包、迁移顺序和验收标准见[《京东云 Firecrawl 多用户注册与本地 AuthProvider 技术方案》](cloudflare-registration-api-key-design.md)。该方案目前仍是设计，不是已部署事实。

## 11. 当前结论

京东云上已经建立一套可用的私有 Firecrawl 服务，并通过 Nginx Bearer API Key 完成入口鉴权。无 Key 和错误 Key 被拒绝，正确 Key 可以访问健康接口并执行真实抓取与搜索；Firecrawl 和全部内部依赖保持最小网络暴露。

因此，“在京东云建立一个可以鉴权的 Firecrawl 服务”这一阶段目标已经实现并通过验证。完整多用户身份、权限、配额和租户隔离仍属于下一阶段；其技术路线已经确定为 Firecrawl 内嵌本地 AuthProvider 与本地配额/资源准入，不要求单独创建 Gateway，但尚未实施和验收。
