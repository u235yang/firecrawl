# Cloudflare 公网注册与用户 API Key 技术方案

> 文档状态：方案设计，已补充数据面与隔离方案，待阶段 0 决策冻结，尚未实施
>
> 编写日期：2026-08-09
>
> 当前基线：京东云自托管 Firecrawl、Nginx 单一 Bearer Key 鉴权、私有 SSH 隧道

## 1. 目标

在现有京东云 Firecrawl 服务前增加 Cloudflare 公网入口、用户注册控制台和用户级 API Key 控制面，使用户能够：

1. 通过域名访问注册页面；
2. 注册并完成必要的邮箱和人机验证；
3. 获得独立、可撤销的 API Key；
4. 将 API URL 和 Key 配置到 Firecrawl Skill 或 SDK；
5. 通过统一 API 域名调用 Firecrawl；
6. 在服务端保留用户、Key 元数据、用量和任务归属记录。

本方案的目标不只是让多个用户共享一个入口，还要避免用户之间相互访问任务、无限占用资源或绕过鉴权。

## 2. 关键结论

方案技术上可行，但不能把 Firecrawl API 的未鉴权请求直接 `302` 跳转到注册页面。

浏览器可以处理网页跳转，Firecrawl CLI、SDK 和 Skill 期望得到 JSON API 响应。如果 `/v1/*` 返回 HTML 注册页面，客户端通常会报协议或解析错误。

推荐行为如下：

```text
无 Key 调用 API
  -> HTTP 401
  -> JSON 返回 registration_required 和 registration_url
  -> Skill 提示用户打开注册地址
  -> 用户注册并获得 Key
  -> 用户安全保存 Key
  -> 重新调用 API
```

如果要求真正自动打开浏览器，需要修改 Skill 或增加包装 CLI，使其识别 `401 registration_required`。这不是 Firecrawl SDK 默认具备的行为。

本方案还冻结以下原则：

1. 用户级 Key 校验不等于多用户隔离；
2. `Nginx auth_request` 只能做入口鉴权判定，不能承担任务归属、响应解析和用量结算；
3. 公网 API 的主链路必须经过专用 API Gateway，不得由 Nginx 直接把全部 `/v1/*` 或 `/v2/*` 透传给 Firecrawl；
4. API Gateway 必须采用默认拒绝的接口白名单；
5. 对允许的异步接口，任务所有权必须与该接口同时上线，不能先开放任务创建、后补查询和取消隔离；
6. 配额与并发必须使用原子预占、结算和回收机制，不能只在鉴权请求中做一次非原子查询；
7. 当前 4 GB 主机只适合作为小规模邀请制验证环境，不适合公开无限注册；
8. 在每个实施阶段达到本阶段验收标准前，不得把该阶段描述为已完成。

## 3. 推荐总体架构

### 3.1 三个职责平面

方案分为边缘入口、控制面和数据面。三者职责必须分离：

| 平面 | 组件 | 主要职责 |
| --- | --- | --- |
| 边缘入口 | Cloudflare DNS、TLS、WAF、Tunnel | 域名、TLS、防护、边缘限流和到源站的出站隧道 |
| 控制面 | Console、Auth Service、用户数据库 | 注册、登录、邀请、Key 生命周期、用户状态、策略配置和管理审计 |
| 数据面 | Nginx、API Gateway、配额存储、任务协调器 | API 鉴权、路由授权、额度预占、任务归属、用量结算和 Firecrawl 代理 |

Auth Service 可以与 API Gateway 复用同一个轻量应用和代码库，但接口、权限、日志和运行身份仍应按控制面与数据面分开。控制面故障不得自动绕过 API 鉴权；数据面故障应返回明确的 `5xx`，不得降级为匿名访问。

### 3.2 域名与请求链路

建议使用两个子域名，避免网页与 API 响应混淆：

```text
console.example.com
  -> Cloudflare DNS / TLS / WAF
  -> Cloudflare Tunnel
  -> Nginx
  -> Console / Auth Service
  -> 用户数据库

api.example.com
  -> Cloudflare DNS / TLS / WAF
  -> 同一条 Cloudflare Tunnel
  -> Nginx
  -> API Gateway
       1. 校验用户 Key
       2. 执行接口白名单和作用域策略
       3. 原子预占配额与并发
       4. 代理请求并解析响应
       5. 记录任务归属并结算用量
  -> Firecrawl 127.0.0.1:3002
  -> Playwright / PostgreSQL / Redis / RabbitMQ / SearXNG
```

Cloudflare Tunnel 的 Published Application 可以把多个公开主机名映射到本地服务。创建路由时，Cloudflare 会建立指向 Tunnel 子域名的代理 DNS 记录，不需要将域名 A 记录指向京东云公网 IP，也不需要开放入站 `80/443`。

推荐保留以下网络边界：

| 服务 | 建议监听 |
| --- | --- |
| Firecrawl | `127.0.0.1:3002` |
| Nginx 公网隧道入口 | `127.0.0.1:3003` |
| API Gateway | Docker 内网或独立回环端口，例如 `127.0.0.1:3004` |
| Auth Service / Console | Docker 内网或独立回环端口 |
| 用户数据库 | Docker 内网，不映射公网端口 |
| Redis | Docker 内网，不映射公网端口 |
| cloudflared | 只建立出站连接 |

Nginx 按 `Host` 路由 `console` 和 `api`。未识别 Host、未列入白名单的路径和不允许的方法应直接拒绝。Tunnel 只能指向 Nginx，不能指向 API Gateway 或 Firecrawl。

### 3.3 各组件的责任边界

Nginx 负责：

- Host 和路径的第一层默认拒绝；
- 请求体大小、连接数和基础超时；
- 移除外部伪造的 `X-Internal-*` Header；
- 生成或传递请求追踪 ID；
- 将 API 请求转发到 API Gateway；
- 不缓存 API、鉴权和用户控制台的敏感响应。

API Gateway 负责：

- 解析并验证用户 API Key；
- 把 Key 映射到 User ID、Key ID、作用域和配额策略；
- 校验接口、方法、版本、请求参数和资源硬上限；
- 原子预占请求速率、资源单位和并发槽位；
- 代理 Firecrawl 请求并解析同步结果或异步任务 ID；
- 创建和校验任务所有权；
- 结算、回补、超时回收和审计；
- 生成统一的 JSON 错误响应。

Firecrawl 负责：

- 实际抓取、搜索、映射、爬取和结果生成；
- 内部 Worker、Playwright 和队列执行；
- 不直接信任公网传入的用户身份 Header；
- 在 `USE_DB_AUTHENTICATION=false` 期间不承担本项目的用户隔离责任。

### 3.4 为什么不能只使用 `auth_request`

Nginx `auth_request` 根据鉴权子请求的 `2xx`、`401` 或 `403` 决定是否放行，适合做入口身份检查，但它不能可靠完成以下工作：

- 解析 Firecrawl 创建任务后的响应体并取得任务 ID；
- 在查询、取消和 WebSocket 握手前检查任务所有权；
- 按实际完成页数或任务结果结算资源单位；
- 处理异步任务结束后的并发释放和失败回补；
- 生成全部业务错误所需的统一 JSON 响应。

因此，`auth_request` 可以作为过渡或纵深防御，但不能替代主链路中的 API Gateway。阶段 1 起，公网 API 不得采用“`auth_request` 成功后直接透传 Firecrawl”的结构。

## 4. 用户注册与使用流程

### 4.1 注册流程

阶段 1 采用邀请制：

1. 管理员创建一次性、短有效期的邀请；
2. 用户通过邀请链接进入 Console；
3. 服务端校验邀请摘要、状态和有效期；
4. 用户完成邮箱验证并设置密码，或绑定已经批准的 OAuth 身份；
5. Auth Service 创建用户记录并标记邀请已消费；
6. 用户在 Console 创建 API Key；
7. 原始 Key 只显示一次；
8. 数据库只保存公开 Key ID、HMAC 摘要、状态和元数据；
9. 用户丢失 Key 后轮换新 Key，不重新显示旧 Key。

阶段 3 才开放 `https://console.example.com/register` 自助注册，并在上述流程前增加服务端 Turnstile 校验、公开注册限流和滥用检测。Turnstile 必须调用服务端 Siteverify API；仅放置前端组件不能形成有效保护。Turnstile Token 是一次性的，并会在较短时间内过期。

### 4.2 Skill 配置流程

用户最终需要配置两个值：

```text
FIRECRAWL_API_URL=https://api.example.com
FIRECRAWL_API_KEY=<user-key>
```

真实 Key 不应写进 `SKILL.md`。推荐保存位置包括：

- 权限为 `600` 的专用凭证文件；
- 操作系统 Keychain 或秘密管理系统；
- 运行环境注入的环境变量。

Skill 只记录 API URL、凭证读取方式和故障提示，不保存秘密值。

### 4.3 API 调用流程

```text
Client
  -> Authorization: Bearer <user-key>
  -> Nginx
       1. 校验 Host、方法、基础路径和请求大小
       2. 移除外部 X-Internal-* Header
  -> API Gateway
       1. 解析公开 Key ID
       2. 查询 Key 记录并校验 HMAC 摘要
       3. 检查账号、Key 状态、到期、作用域和接口白名单
       4. 原子预占速率、资源单位和并发槽位
       5. 移除用户 Authorization Header
  -> Firecrawl
  -> API Gateway 解析响应
       6. 同步请求立即结算
       7. 异步请求写入任务所有权后再向客户端返回任务 ID
       8. 记录脱敏审计事件
```

API Gateway 可以调用 Auth Service 的内部鉴权接口，也可以使用同一代码库中的鉴权模块，但不得把用户 Key 写成不断增长的 Nginx 配置文件。任何外部 `Authorization`、`X-Internal-User-ID`、`X-Internal-Key-ID` 和配额 Header 都必须在进入内部链路前被移除或覆盖。

## 5. Cloudflare 条件

### 5.1 基础条件

- 域名已经接入 Cloudflare DNS；
- Cloudflare Zone 状态正常并启用 TLS；
- 京东云安装受管理的 `cloudflared` 服务；
- 创建至少两个 Published Application：`console` 和 `api`；
- Tunnel 凭证保存在权限受限的文件或服务配置中；
- 京东云允许 `cloudflared` 出站访问 Cloudflare。

`cloudflared` 主要需要出站 UDP/TCP `7844`，管理和更新还会使用 TCP `443`。Tunnel 不要求开放云主机入站端口。

### 5.2 Cloudflare 安全规则

阶段 1 至少需要：

- 注册和登录接口的 IP 限流；
- 基础 WAF 管理规则；
- `api.example.com` 的 Cache Bypass；
- 管理后台的单独访问控制；
- 隧道在线状态监控。

阶段 3 开放公开注册前，再强制增加 Turnstile、公开注册专用限流和相应的滥用规则。

Cloudflare Access 可以用于内部管理员后台，但不建议把浏览器式 Access 登录直接放在 Firecrawl API 前。机器使用 Access Service Token 时需要额外的 Client ID 和 Client Secret Header，不等同于单一 Firecrawl API Key。

### 5.3 Cloudflare 代理限制

Cloudflare 普通代理请求存在 Origin Read Timeout。长时间同步抓取可能收到 `524`，因此 Firecrawl API 应优先使用：

- 异步创建任务；
- 立即返回任务 ID；
- 客户端轮询任务状态；
- 对同步抓取设置可验证的最大超时。

API 域名必须禁用缓存，避免任务状态、用户信息或错误响应被错误复用。

截至本文编写日，Cloudflare 默认 Proxy Read Timeout 为 125 秒。工程上不应把 125 秒全部留给上游；应为 API Gateway、Nginx 和客户端分别定义更短且可观测的超时预算。创建任务接口必须支持幂等键，避免客户端收到 `524` 或断线后重复创建任务。该数值属于外部动态事实，实施前应再次核对 Cloudflare 当前文档和实际套餐限制。

## 6. 控制面与 API Gateway 条件

### 6.1 Auth Service 用户能力

- 邀请制注册和后续公开注册；
- 登录与安全退出；
- 邮箱验证；
- 密码重置或 OAuth；
- 会话创建、续期、撤销和设备退出；
- 账号冻结和注销；
- 用户协议与隐私政策版本化确认记录；
- 管理员与普通用户的角色隔离；
- 用户数据导出、删除或匿名化流程。

密码必须使用 Argon2id、bcrypt 等合适的密码哈希算法，不能明文或可逆保存。

### 6.2 API Key 生命周期

- 安全随机生成；
- 原始 Key 只显示一次；
- 数据库保存公开 Key ID 和 HMAC 摘要，不保存明文；
- 显示不敏感的 Key ID 和末尾片段用于识别；
- 支持命名、到期、吊销和轮换；
- 支持最小权限作用域；
- 支持最后使用时间和使用来源审计；
- Key 被吊销后立即清除鉴权缓存。

推荐的 Key 格式为：

```text
fc_jd_<public_key_id>_<high_entropy_secret>
```

`public_key_id` 用于索引数据库记录，`high_entropy_secret` 用于计算 HMAC。格式与长度在实施前必须使用目标 Firecrawl CLI、SDK 和 Skill 做兼容性测试。

HMAC 主密钥按版本保存在独立秘密管理机制中。由于数据库不能恢复原始 Key，主密钥轮换时需要在限定窗口内保留旧版本的验证能力，并逐步要求用户重签 Key；不能假设只更新数据库摘要即可完成无感轮换。

### 6.3 内部鉴权接口

如果 Auth Service 与 API Gateway 分开部署，需要仅供 API Gateway 调用的内部接口，例如：

```text
POST /internal/v1/keys/verify
```

内部请求传入原始 Key、目标接口和请求方法，但不得把 Key 放入 URL、普通访问日志或进程参数。成功返回：

```json
{
  "active": true,
  "user_id": "<id>",
  "api_key_id": "<id>",
  "scopes": ["scrape:write", "crawl:read"],
  "quota_policy_id": "<id>"
}
```

失败返回 `401` 或 `403` 和内部错误码。该接口不能通过 Cloudflare 或公网访问，并应使用独立服务身份、Unix Socket、mTLS 或等价的内部认证机制，不能只依赖“别人不知道端口”。

### 6.4 API Gateway 必需能力

API Gateway 必须实现：

- Key 验证与用户状态检查；
- 默认拒绝的接口白名单和方法白名单；
- 请求参数归一化、体积限制和资源硬上限；
- 原子速率、配额和并发预占；
- 同步响应结算；
- 异步任务 ID 捕获和所有权写入；
- 查询、取消、错误列表和结果读取前的所有权检查；
- 后台任务对账与过期租约回收；
- 统一错误协议和请求追踪 ID；
- 结构化、脱敏的用量和安全审计。

API Gateway 对 Auth Service、配额存储或所有权数据库的依赖应采用“失败关闭”：无法确认身份、配额或任务归属时拒绝请求，不能直接放行 Firecrawl。

## 7. 数据库条件

建议建立独立数据库和最小权限账号，不直接混入 Firecrawl 内部业务表。

### 7.1 最小数据模型

```text
users
- id
- email_normalized
- password_hash
- email_verified_at
- status
- role
- terms_version
- terms_accepted_at
- created_at
- updated_at

auth_identities
- id
- user_id
- provider
- provider_subject
- created_at

user_sessions
- id
- user_id
- refresh_token_digest
- expires_at
- revoked_at
- created_at

auth_tokens
- id
- user_id
- token_type
- token_digest
- expires_at
- consumed_at
- created_at

api_keys
- id
- user_id
- name
- public_key_id
- key_digest
- digest_version
- scopes
- expires_at
- revoked_at
- last_used_at
- created_at

quota_policies
- id
- name
- requests_per_minute
- daily_resource_units
- user_concurrency
- key_concurrency
- created_at

quota_reservations
- id
- user_id
- api_key_id
- endpoint
- request_id
- reserved_units
- settled_units
- status
- lease_expires_at
- upstream_task_id
- created_at
- settled_at

idempotency_records
- id
- user_id
- api_key_id
- idempotency_key_digest
- request_fingerprint
- request_id
- status
- upstream_task_id
- response_snapshot
- expires_at
- created_at
- updated_at

task_ownership
- upstream_task_id
- user_id
- api_key_id
- request_id
- task_type
- status
- created_at
- completed_at

usage_events
- id
- user_id
- api_key_id
- request_id
- endpoint
- upstream_task_id
- resource_units
- outcome
- duration_ms
- created_at

audit_events
- id
- actor_type
- actor_id
- action
- target_type
- target_id
- request_id
- outcome
- created_at
```

`idempotency_records`、`quota_reservations`、`task_ownership` 和 `usage_events` 应通过 `request_id` 关联。邮箱应使用规范化后的唯一索引；OAuth 身份应对 `(provider, provider_subject)` 建立唯一约束；任务所有权应对 `upstream_task_id` 建立唯一约束；幂等记录应对 `(user_id, idempotency_key_digest)` 建立唯一约束。

`response_snapshot` 只保存状态码、任务 ID 和重放响应所需的非敏感元数据，不保存完整抓取结果、用户秘密 Header 或任意网页正文。

Redis 可以保存短期速率、并发计数和租约，但数据库仍需保存可审计的预占、任务和最终用量记录。不得把 Redis 中易失的计数当作唯一账本。

### 7.2 为什么不能保存明文 Key

如果数据库泄露，明文 Key 会立即成为可用凭证。正确设计是：

- Key 使用高熵随机值；
- 用户只在创建时看到一次；
- 服务端使用与数据库分离保存的主密钥计算 HMAC 摘要；
- 数据库保存 `digest_version`，支持主密钥轮换；
- 校验时对输入 Key 计算相同 HMAC；
- 使用常量时间比较；
- 无法恢复旧 Key，只能重新签发。

如果业务强制要求以后再次显示原始 Key，就必须使用独立 KMS 加密保存，但这会显著扩大安全和运维责任，不建议作为第一版设计。

## 8. 多用户隔离条件

只验证 Key 不等于完成多用户服务。必须处理以下问题：

### 8.1 任务所有权

Firecrawl 的 crawl、batch、extract 等异步接口会返回任务 ID。必须记录任务 ID 属于哪个用户，并在查询、取消和读取结果时再次校验所有权，防止用户 A 访问用户 B 的任务。

阶段 1 采用 API Gateway 记录和校验任务所有权，处理顺序如下：

1. 客户端携带 Key 和 `X-Idempotency-Key` 发起任务创建；
2. API Gateway 计算请求指纹，并以用户 ID 命名空间保存幂等记录；
3. 相同幂等键和相同请求返回既有结果，相同幂等键和不同请求返回 `409 idempotency_conflict`；
4. API Gateway 鉴权并原子预占配额；
5. API Gateway 生成不会跨用户碰撞的上游幂等键并把请求转发给 Firecrawl；
6. Firecrawl 返回任务 ID；
7. API Gateway 在数据库事务中写入 `task_ownership`、完成幂等记录并关联 `quota_reservations`；
8. 只有所有权写入成功后，API Gateway 才把任务 ID 返回客户端；
9. 如果所有权写入失败，API Gateway 返回 `503 ownership_persist_failed`，尝试取消上游任务，并记录孤儿任务告警；
10. 查询、取消、错误读取和结果读取请求先查询所有权；
11. 不属于当前用户或不存在的任务统一返回 `404 task_not_found`，避免泄露任务是否存在。

任务 ID 的高熵和不可猜测性只能降低碰撞或扫描概率，不能替代授权检查。

WebSocket 连接必须在握手时同时验证 Key 和任务所有权。阶段 1 不开放 WebSocket；在 API Gateway 能完成握手鉴权、所有权检查、连接数限制和断线审计前不得上线。

### 8.2 API 接口白名单

公网 API 必须采用“默认拒绝、逐项开放”。阶段 1 的初始白名单如下：

| 接口 | 阶段 1 | 作用域 | 所有权要求 | 说明 |
| --- | --- | --- | --- | --- |
| `POST /v1/scrape` | 允许 | `scrape:write` | 同步结算 | 请求体、超时和页面大小设硬上限 |
| `POST /v1/search` | 允许 | `search:write` | 同步结算 | 限制结果数和是否抓取结果页 |
| `POST /v1/map` | 允许 | `map:write` | 同步结算 | 限制 URL 数和执行超时 |
| `POST /v1/crawl` | 允许 | `crawl:write` | 创建后写入所有权 | 强制幂等键，限制页数、深度和任务时长 |
| `GET /v1/crawl/:jobId` | 允许 | `crawl:read` | 访问前校验 | 所有权不匹配返回 404 |
| `DELETE /v1/crawl/:jobId` | 允许 | `crawl:cancel` | 访问前校验 | 记录取消人和结算结果 |
| `GET /v1/crawl/:jobId/errors` | 允许 | `crawl:read` | 访问前校验 | 不返回其他用户信息 |

阶段 1 默认拒绝：

- 全部 `/v0/*`；
- 未逐项验证的 `/v2/*`；
- batch、extract、agent、browser、fireclaw 和 x402；
- `/team/*`、队列、管理、指标和内部健康接口；
- Firecrawl 的内部 webhook 和注册类接口；
- Crawl WebSocket。

阶段 2 若需要开放 v2、batch、extract、agent 或 browser，必须先为对应任务、会话、交互和销毁路径补齐所有权、资源计费和跨用户测试，然后逐项加入白名单。上游升级新增路由时不得自动继承公网访问权限。

### 8.3 配额和并发

至少需要四层限制：

| 限制 | 维度 | 目的 |
| --- | --- | --- |
| 请求速率 | 每 Key、每接口、每分钟 | 防止高频滥用和认证撞库 |
| 日资源额度 | 每用户、每日 | 限制总体资源消耗 |
| 用户并发 | 每用户、每 Key | 防止单用户独占队列 |
| 全局并发 | 全服务、Playwright、Worker | 保护 4 GB 主机和内部依赖 |

只限制 HTTP 请求次数仍不够，因为一个 Scrape 和一个大型 Crawl 的资源成本差异很大。资源单位应按接口建立可配置规则，第一版建议采用：

- Scrape：按页面数和是否使用浏览器计费；
- Search：基础搜索单位，加上实际抓取的结果页数量；
- Map：按实际返回 URL 数分段计费；
- Crawl：按预期最大页数预占，按实际完成页数结算；
- 失败任务：保留已实际消耗部分，其余回补；
- 客户端取消：按已经完成的工作结算，不全额退还。

具体数值、最大页数、最大结果数和日额度属于阶段 0 决策，必须通过串行容量测试确定，不在设计文档中伪装成已经验证的容量结论。

每次请求的原子流程为：

1. 根据 Key、用户、接口和请求参数估算预占单位；
2. 使用 Redis Lua、数据库事务或等价机制，同时检查并增加速率、日额度、用户并发和全局并发；
3. 写入带 TTL 的 `quota_reservations` 租约；
4. 同步请求完成时立即结算和释放；
5. 异步请求由任务状态轮询、回调或后台对账器结算；
6. Gateway 崩溃或任务失联时，由租约回收器标记异常并释放并发；
7. 结算动作使用 `request_id` 保证幂等，不能重复扣减或重复回补。

阶段 1 全局真实抓取和 Playwright 并发保持为 `1`。即使 HTTP 并发较高，也只能进入有界队列；队列满时返回 `429 global_capacity_exceeded`，不得无限堆积任务。

### 8.4 日志和审计

日志可以记录：

- User ID；
- Key ID；
- 请求路径；
- Firecrawl 任务 ID；
- 状态码；
- 延迟与资源单位。

日志不得记录：

- 完整 Authorization Header；
- 原始 API Key；
- 密码；
- 邮箱验证 Token；
- URL 中的敏感查询参数；
- 用户提交的秘密 Header。

访问日志应使用规范化后的路由模板，例如 `/v1/crawl/:jobId`，不要把完整目标 URL 或敏感查询参数直接作为标签。高基数字段和原始请求体不应进入指标系统。审计记录应区分用户操作、管理员操作、后台结算和系统故障，并定义保存、查询、导出和删除周期。

## 9. 当前 4 GB 服务器的容量边界

现有京东云约为 2 vCPU、4 GB 内存，并配置为一个 Firecrawl Worker 和一个 Playwright 页面。

在该规格下，可以实施邀请制、小用户量 MVP，但应满足：

- 全局真实抓取并发保持为 `1`；
- 限制注册用户总数；
- 每用户设置较低日配额；
- Auth Service、API Gateway 和后台对账器优先采用同一轻量运行时，避免为第一版引入多个重型服务；
- 用户数据库优先使用托管服务，或在现有 PostgreSQL 中建立独立数据库和账号；
- 对 Crawl 页数、Map URL 数、Search 结果数、请求体大小、同步超时和队列长度设置硬上限；
- 上线前执行内存、Swap、队列积压和 OOM 压力测试。

公开无限注册不适合当前容量。正式开放前建议：

- 将服务器升级到至少 8 GB，或把控制面数据库迁移到托管服务；
- 根据实测增加 Firecrawl Worker 和 Playwright 容量；
- 增加全局排队、背压和降级策略；
- 必要时拆分 Auth、数据库和 Firecrawl 主机。

8 GB 是初始工程建议，不是未经测试即可保证的容量结论，最终规格必须由并发和任务模型压测确定。

## 10. Skill 配合条件

### 10.1 统一错误协议

所有 API Gateway 错误响应使用 JSON，不返回 HTML。为了兼容可能读取 Firecrawl `error` 字符串的现有客户端，同时提供机器可读错误码，统一格式为：

```json
{
  "success": false,
  "error": "API key is required",
  "error_code": "registration_required",
  "registration_url": "https://console.example.com/register",
  "request_id": "req_..."
}
```

`error` 是可读消息，`error_code` 是稳定的机器判断字段。可选字段包括 `registration_url`、`retry_after_seconds`、`quota_remaining` 和 `reset_at`。不得根据可读消息文本实现客户端分支。

推荐状态映射：

| HTTP | `error_code` | 适用情况 | 是否可重试 |
| --- | --- | --- | --- |
| `401` | `registration_required` | 未提供 Key | 用户注册或配置 Key 后重试 |
| `401` | `invalid_key` | Key 格式错误或不存在 | 修正或轮换 Key |
| `401` | `key_expired` | Key 已到期 | 轮换 Key |
| `401` | `key_revoked` | Key 已吊销 | 轮换 Key |
| `403` | `account_suspended` | 账号被冻结 | 联系管理员 |
| `403` | `scope_denied` | Key 无目标接口作用域 | 更换 Key 或授权 |
| `404` | `task_not_found` | 任务不存在或不属于当前用户 | 不自动重试 |
| `404` | `endpoint_not_enabled` | 接口未列入当前阶段白名单 | 不自动重试 |
| `409` | `idempotency_conflict` | 幂等键对应不同请求 | 更换幂等键或修正请求 |
| `429` | `quota_exceeded` | 日额度不足 | 等待额度恢复 |
| `429` | `concurrency_exceeded` | 用户或 Key 并发超限 | 按 `retry_after_seconds` 重试 |
| `429` | `global_capacity_exceeded` | 全局有界队列已满 | 退避重试 |
| `503` | `auth_dependency_unavailable` | 无法安全验证身份 | 退避重试，不得绕过鉴权 |
| `503` | `ownership_persist_failed` | 上游任务已创建但归属写入失败 | 不盲目重试，使用幂等键查询 |

结构化错误由 API Gateway 生成。不得假设 Nginx `auth_request` 会把鉴权子请求的 JSON Body 自动透传给客户端。

### 10.2 Skill 行为

推荐 Skill 在调用 Firecrawl 时区分以下响应：

| 状态 | Skill 行为 |
| --- | --- |
| `401 registration_required` | 显示注册地址，停止业务调用 |
| `401 invalid_key` | 提示检查或轮换 Key |
| `403 account_suspended` | 提示联系管理员 |
| `429 quota_exceeded` | 显示配额和恢复时间 |
| `429 concurrency_exceeded` | 等待后重试 |
| `429 global_capacity_exceeded` | 按服务端建议退避 |
| `5xx` | 报告服务故障，不提示重新注册 |

如果希望自动打开注册页面，需要额外实现包装 CLI 或 Device Authorization 类流程；不能依赖标准 Firecrawl SDK 自动弹出浏览器。

还需要对所有目标客户端验证 Key 格式兼容性，避免生成会被某些 CLI 解析或缩短的特殊前缀。

## 11. 安全与合规条件

### 11.1 技术安全

- Firecrawl `3002` 不得被 Tunnel 或公网直接访问；
- Tunnel 只能指向 Nginx 入口；
- Nginx 只能把公网 API 转发给 API Gateway，不能直接转发给 Firecrawl；
- API Gateway、Auth Service、数据库和 Redis 只走内部网络；
- 管理、队列和健康后台不能公开；
- API Gateway 默认拒绝未知版本、未知路径和未知方法；
- 外部传入的 `X-Internal-*`、`X-User-*` 和类似身份 Header 必须移除；
- 只有来自受信任 Tunnel 链路的 Cloudflare 客户端 IP Header 才可用于安全审计和限流；
- Console Cookie 使用 `Secure`、`HttpOnly` 和合适的 `SameSite` 策略；修改状态的浏览器请求必须具备 CSRF 防护；
- Console 和 API 分别设置严格 CORS，API 不因浏览器跨域便利而允许任意 Origin；
- 保持 Firecrawl 对私网地址和云元数据地址的 SSRF 防护；
- 对用户提交的请求 Header、Cookie、代理、Webhook 和目标 URL 进行策略过滤，防止携带秘密或访问禁止网段；
- 数据库执行备份和恢复演练；
- 凭证支持轮换和吊销；
- 敏感日志执行自动扫描；
- 注册、登录、Key 创建和 API 调用均需要限流。

### 11.2 中国大陆合规

如果京东云服务器位于中国大陆并通过域名公开提供服务，需要确认：

- ICP 备案或经营性 ICP 许可；
- 首页展示备案号；
- 用户协议和隐私政策；
- 注册信息与日志的保存、删除和导出规则；
- 数据安全和个人信息保护要求；
- 公开爬取服务的可接受使用政策与滥用处理机制。

Cloudflare China Network 是独立的 Enterprise 订阅，并要求有效 ICP。普通 Cloudflare 全球网络访问中国大陆源站可能还需要评估跨境延迟和稳定性。

上述内容是工程侧待核对事项，不构成法律结论。公开上线前应由具备相应职责的人员确认备案、许可、个人信息处理、日志留存、数据跨境、服务条款和滥用处置要求。

## 12. 实施阶段

### 阶段 0：决策冻结

阶段 0 不部署公网入口。必须先形成 ADR、策略表和测试计划，至少冻结：

- 域名与两个子域名；
- 数据面采用 API Gateway，不采用 Nginx 直接透传；
- 阶段 1 只开放本方案列出的 v1 接口白名单；
- 注册方式采用邀请制，公开自助注册推迟到阶段 3；
- 用户是否收费；
- 各接口资源单位、硬上限、初始配额和并发；
- Key 到期规则；
- 用户数据库使用托管服务还是现有 PostgreSQL 的独立数据库和账号；
- 现有共享 Key 的迁移、到期和撤销时间；
- ICP 与隐私合规路径；
- 是否接受当前 4 GB 邀请制 MVP；
- 阶段 1 验收用例、失败处理和回滚责任人。

阶段 0 的交付物至少包括：数据面 ADR、接口白名单、错误码表、配额策略、数据模型、威胁模型、迁移回滚方案和阶段 1 验收计划。

### 阶段 1：邀请制 MVP

- Cloudflare Tunnel；
- `console` 与 `api` 两个域名；
- Nginx Host 路由、默认拒绝和 Cache Bypass；
- API Gateway 主链路；
- 邀请制 Console 和 Auth Service；
- 用户数据库；
- 用户级 Key 生成、HMAC 校验、作用域和吊销；
- 本方案列出的 v1 接口白名单；
- Crawl 创建、状态、错误和取消的任务所有权；
- 原子配额预占、结算、租约回收和有界队列；
- 全局并发 `1`；
- 用户级用量与脱敏审计；
- Skill 手工配置 Key。

阶段 1 不包含公开自助注册、v2、batch、extract、agent、browser、WebSocket、高可用和付费。它只有在跨用户任务隔离通过验收后，才能称为“邀请制多用户 MVP”。

### 阶段 2：接口扩展与运营能力

- 按接口逐项开放 v2、batch 和 extract；
- 为新增接口补齐任务所有权、资源计费和跨用户测试；
- 经独立评估后再开放 WebSocket、agent 和 browser；
- 更完善的用户公平队列、并发和资源配额；
- 用量统计与对账；
- 管理后台；
- Key 轮换与过期；
- 告警、滥用处置和运营报表。

### 阶段 3：公开注册与扩容

- 公开注册、Turnstile 和邮箱验证；
- WAF 与边缘限流；
- 容量扩展；
- 数据库高可用与备份；
- Tunnel 和源站高可用；
- 公开服务合规验收；
- 滥用监控和运营流程。

### 12.4 迁移与回滚顺序

建议按以下顺序迁移：

1. 备份并记录当前 Nginx、Firecrawl Compose、LaunchAgent 和凭证配置的安全版本；
2. 在仅回环或 Docker 内网可达的条件下部署 Auth Service、数据库和 API Gateway；
3. 通过现有 SSH 隧道验证 Gateway 的无 Key、错误 Key、正确 Key、白名单、真实 Scrape、Search 和 Crawl；
4. 把现有共享 Key 迁移为有明确所有者、作用域和到期时间的临时服务凭证，不保留永久绕过身份；
5. 完成阶段 1 跨用户、配额、日志和容量验收；
6. 最后创建 Cloudflare Tunnel 公网路由和边缘规则；
7. 观察稳定后撤销旧共享 Key 或将其限制为管理员维护用途。

回滚时优先禁用 Cloudflare Published Application，使服务退回私有 SSH 隧道入口；随后恢复经过验证的 Nginx、Gateway 或应用配置。回滚不得把 Tunnel 改为直连 Firecrawl，也不得以取消鉴权为默认结果。

## 13. 验收标准

### 13.1 阶段 0 验收

1. 数据面 ADR 明确 API Gateway、Auth Service、Nginx 和 Firecrawl 的责任；
2. 接口白名单、错误协议、数据模型和配额策略无未决冲突；
3. 域名、注册模式、数据库位置、Key 到期、容量和合规路径已经确认；
4. 迁移、回滚、验收用例和责任边界已经书面化；
5. 未实施能力仍明确标注为设计，不描述为已上线。

### 13.2 阶段 1 验收

1. `console` 与 `api` 域名只能通过 Cloudflare Tunnel 到达 Nginx；
2. Tunnel、Nginx 和 API Gateway 都不能绕过鉴权直达 Firecrawl `3002`；
3. 未识别 Host、未知路径、未知方法、v0、v2 和阶段 1 禁用接口均被默认拒绝；
4. 邀请、登录、退出、账号冻结和 Key 创建、到期、吊销流程正常；
5. 原始 Key 只显示一次，数据库不存在明文 Key，日志不存在 Authorization Header 或完整 Key；
6. 无 Key、错误 Key、过期 Key、吊销 Key和冻结账号均返回约定的 JSON 错误；
7. 正确 Key 能通过公网域名完成一次真实 Scrape、Search、Map 和 Crawl；
8. Crawl 创建成功后存在唯一任务所有权记录；
9. 用户 A 无法查询、读取错误或取消用户 B 的 Crawl，HTTP 与日志均不泄露任务存在性；
10. 配额预占、同步结算、异步结算、失败回补、租约超时和幂等重试均通过测试；
11. 全局真实抓取并发为 `1`，有界队列满时返回 `429`，没有无界积压；
12. Cloudflare、Nginx 和 Gateway 不缓存 API、鉴权或用户响应；
13. 长任务超时或连接断开后不会重复创建任务，也不会产生未记录的永久孤儿任务；
14. 数据库备份可以恢复用户、Key 元数据、任务所有权和配额记录；
15. Skill 可以正确配置 API URL 与 Key，并识别约定错误码；
16. 目标负载下无 OOM、异常重启、持续 Swap 抖动和失控队列；
17. 回滚演练能够关闭公网入口并恢复私有鉴权访问，且不直连 Firecrawl。

### 13.3 阶段 2 验收

1. 每个新增接口都有白名单、作用域、资源模型、所有权规则和成功/失败测试；
2. batch、extract、v2、WebSocket、agent 或 browser 仅在各自验收通过后开放；
3. 多用户并发、公平队列、取消、失败和对账不会相互污染；
4. 管理员操作具备独立授权与审计，不能读取或修改超出职责的数据；
5. 用量报表与任务和预占记录能够对账。

### 13.4 阶段 3 验收

1. 公开注册、邮箱验证、Turnstile、WAF 和边缘限流正常；
2. 备案、隐私、用户协议、可接受使用政策和滥用处置完成相应验收；
3. 容量扩展、高可用、备份恢复和故障切换达到目标；
4. 公开负载下无跨用户数据访问、资源失控或敏感信息泄露。

## 14. 当前项目差距

当前已经具备：

- 京东云 Firecrawl；
- Nginx 鉴权入口；
- 单一共享 Bearer Key；
- 回环监听；
- 本机 SSH 隧道；
- 真实抓取与搜索验证基线。

尚未实现：

- 域名与 Cloudflare Tunnel；
- 注册门户；
- Auth Service；
- API Gateway 和默认拒绝的接口白名单；
- 用户数据库和数据模型；
- 用户级 Key 签发与验证；
- 邮箱和 Turnstile；
- 原子配额、并发预占、结算和租约回收；
- Firecrawl 任务所有权隔离；
- 后台任务对账与孤儿任务处理；
- Skill 的注册引导；
- ICP、隐私和公开服务合规验收。

因此，该方案的核心不是简单增加一个注册页面，而是建立“用户与 Key 控制面 + 强制经过的多用户数据面”。当前项目只达到私有入口和共享 Bearer Key 鉴权基线，尚未达到邀请制多用户 MVP。

## 15. 官方参考资料

- [Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/)
- [Cloudflare Tunnel Routing](https://developers.cloudflare.com/tunnel/routing/)
- [Cloudflare Tunnel Configuration](https://developers.cloudflare.com/tunnel/configuration/)
- [Cloudflare Turnstile 服务端验证](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/)
- [Cloudflare Connection Limits](https://developers.cloudflare.com/fundamentals/reference/connection-limits/)
- [Cloudflare Rate Limiting](https://developers.cloudflare.com/waf/rate-limiting-rules/)
- [Cloudflare Cache Rules](https://developers.cloudflare.com/cache/how-to/cache-rules/settings/)
- [Cloudflare Access Service Tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)
- [Cloudflare China Network ICP](https://developers.cloudflare.com/china-network/concepts/icp/)
- [Nginx auth_request](https://nginx.org/en/docs/http/ngx_http_auth_request_module.html)
- [Firecrawl SELF_HOST](https://github.com/firecrawl/firecrawl/blob/main/SELF_HOST.md)
