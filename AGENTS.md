# Firecrawl 京东云项目说明

## 项目目标

本项目当前阶段的目标是在京东云上搭建并持续运营一套具备鉴权能力的自托管 Firecrawl 服务，为获授权的用户或自动化 Agent 提供稳定、安全的网页抓取、搜索、站点映射和爬取能力。

当前阶段以私有网络入口和 Bearer API Key 鉴权为成功边界。后续如要扩展为完整的多用户服务，还应明确认证模型、用户或调用方身份、权限边界、并发与配额、资源隔离、审计和故障影响范围。在这些能力完成验证前，不得把共享单一 API Key 或容器已经启动描述为完整的多用户交付。

## 整体项目目录结构

项目按“上游源码、京东云基础设施、运维工具、验收测试和项目文档”组织。目标结构如下：

```text
firecrawl/
├── AGENTS.md                 # 项目目标、边界和协作约定
├── README.md                 # Firecrawl 上游项目说明
├── SELF_HOST.md              # Firecrawl 上游自托管说明
├── CONTRIBUTING.md           # 上游贡献指南
├── docker-compose.yaml       # 上游自托管 Compose 基线
├── apps/                     # Firecrawl 上游源码
│   ├── api/                  # API、Worker 和 E2E/snips 测试
│   └── *-sdk/                # 各语言 SDK
├── examples/                 # 上游示例
├── img/                      # 上游图片资源
├── .github/                  # CI、Issue 和 PR 配置
├── docs/                     # 本项目全部设计、部署、运维和验收文档
├── infra/                    # 京东云及本机接入所需的基础设施配置
│   ├── docker/               # 生产 Compose、镜像和资源配置
│   ├── nginx/                # Nginx 鉴权与反向代理配置
│   └── launchd/              # macOS SSH 隧道 LaunchAgent 配置
├── scripts/                  # 不包含秘密值的自动化脚本
│   ├── deploy/               # 部署、升级与回滚脚本
│   ├── operations/           # 巡检、备份与故障诊断脚本
│   └── validation/           # 鉴权、抓取、搜索和容量验证脚本
└── tests/
    └── acceptance/           # 京东云部署与多用户业务验收测试
```

其中 `apps/`、`examples/`、`img/` 和 `.github/` 属于上游工程结构；`docs/`、`infra/`、`scripts/` 和 `tests/acceptance/` 用于本项目的京东云交付。规划目录应在出现真实内容时再创建，不为满足目录外观而建立空目录。

`docs/` 只存放文档。文档可以先直接放在 `docs/` 根目录，除非内容规模确实需要且用户明确同意，否则不要在其中预建分类子目录。部署配置放入 `infra/`，可执行工具放入 `scripts/`，验收代码放入 `tests/acceptance/`，不得混入 `docs/`。

## 当前部署基线

- 京东云服务器通过 SSH 别名 `jingdong-vps` 管理，Firecrawl 部署目录为 `/opt/firecrawl`。
- Firecrawl 仅监听云主机回环地址 `127.0.0.1:3002`。
- Nginx 仅监听 `127.0.0.1:3003`，通过 Bearer API Key 保护 Firecrawl 入口。
- 本机通过 LaunchAgent 维护 SSH 隧道：`127.0.0.1:3002 -> jingdong-vps:127.0.0.1:3003 -> Firecrawl:127.0.0.1:3002`。
- 当前 `USE_DB_AUTHENTICATION=false`；Nginx Bearer 鉴权是现阶段的入口保护，不等同于最终的多用户身份与权限体系。
- 当前服务器约为 2 vCPU、4 GB 内存的受限单机部署。资源规格、端口、容器状态和运行配置都属于动态事实，每次实施或验收前必须实时复核。

## 多用户建设方向

在设计和实施多用户能力时，至少应覆盖以下方面：

1. 为不同用户、团队或调用方提供可撤销、可轮换、可追踪的独立凭证，避免长期共享同一个 Key。
2. 定义用户级并发、速率、任务队列和资源配额，防止单个调用方耗尽 CPU、内存、Playwright 或搜索容量。
3. 保留不泄露 URL 敏感参数、请求头和凭证的审计记录，并能够按调用方定位失败和资源消耗。
4. 明确数据保留、任务结果访问、管理权限和故障隔离边界。
5. 在容量不足时采用可观测、可降级的控制方式，不以无界提高并发代替容量规划。

具体采用 Nginx 独立 Key、Firecrawl 原生数据库认证、外部 API 网关或其他方案，应先形成设计与验收标准，再实施；不要把尚未确认的技术路线写成既定事实。

## 安全与运维原则

- 不得在代码、文档、命令参数、日志、提交或回复中输出真实 API Key、密码、私钥、Token 或 `.env` 内容。
- 凭证必须存放在权限受限的文件或专用秘密管理机制中；健康检查应使用现有安全脚本传递凭证。
- 不得回退到 Firecrawl 官方云 API；本项目使用京东云上的自托管实例。
- 除非用户明确授权，不修改 Cloudflare、安全组、防火墙、公开端口或其他相邻基础设施。
- 保持 Firecrawl、数据库、Redis、RabbitMQ、Playwright 和 SearXNG 仅暴露必要的最小网络面。
- 服务器资源有限，未完成容量验证前一次只运行一个真实抓取、搜索、映射或爬取测试。
- 任何重启、升级、迁移或配置变更前，先检查当前状态、备份、影响范围和回滚路径。

## 验证要求

服务验收不能只看容器是否为 `Up`。至少需要验证：

- Nginx 与 Firecrawl 的实际监听地址和端口；
- 无 Key、错误 Key 和正确 Key 的鉴权结果；
- Firecrawl 健康响应；
- 一次真实网页抓取和一次真实搜索；
- API、Playwright、PostgreSQL、Redis、RabbitMQ 和 SearXNG 的运行与健康状态；
- OOM、异常重启、严重错误日志和敏感信息泄露情况；
- 多用户阶段的独立身份、限流、配额、审计和相互影响测试。

本机鉴权健康检查使用：

```bash
bash /Users/nikcel/.agents/skills/firecrawl/scripts/health-check.sh
```

每次交付都应明确说明：已实现内容、验证证据、仍未实现的多用户能力，以及是否达到当前阶段的验收标准。

## 代码修改约定

- Firecrawl API 和 Worker 代码位于 `apps/api`；各语言 SDK 位于 `apps/*-sdk`。
- 修改 API 行为时优先补充覆盖成功路径和失败路径的 E2E/snips 测试。
- 只运行与变更直接相关的本地测试，把完整测试矩阵交给 CI。
- 保留用户已有的已跟踪和未跟踪修改，不执行破坏性 Git 操作。
